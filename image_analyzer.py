"""
image_analyzer.py – Describe images or answer questions about them using SmolVLM.

Uses HuggingFaceTB/SmolVLM-Instruct locally on CPU (no GPU needed).
The model is downloaded once (~2 GB) and then cached by HuggingFace.

Quick-start
-----------
    from image_analyzer import ImageAnalyzer

    analyzer = ImageAnalyzer()
    await analyzer.warmup()   # pre-load model at startup (recommended)

    # Free description
    with open("photo.jpg", "rb") as f:
        description = await analyzer.describe(f.read())
    print(description)
    # → "The image shows a living room with the lights on. A person
    #    is sitting on the sofa reading a book."

    # Targeted question
    answer = await analyzer.describe(image_bytes, prompt="Is the light on?")
    # → "Yes, the ceiling light is on and the room is well lit."

    # Combined with MatrixReceiver:
    async for msg in receiver.messages():
        if msg.type == "image":
            description = await analyzer.describe(msg.data)
            await receiver.send(msg.room_id, f"🖼️ {description}")

Why SmolVLM over BLIP?
----------------------
    BLIP always outputs a single short caption with no ability to ask questions.
    SmolVLM is a full Vision Language Model: you can pass any text prompt and
    get a detailed, conversational answer. On CPU it takes ~15–30 seconds per
    image (vs ~3–5 s for BLIP), which is fine for home-automation use cases.

Installation
------------
    pip install transformers torch Pillow

The model (~2 GB) is downloaded automatically on first use to:
    ~/.cache/huggingface/hub/
"""

import asyncio
import io
import logging
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "HuggingFaceTB/SmolVLM-Instruct"

_DEFAULT_PROMPT = (
    "Describe this image in detail. "
    "Mention objects, people, lighting, and anything relevant for home automation. Keep the answers short and precise."
)


class ImageAnalyzerError(Exception):
    """Raised when the model cannot load or inference fails."""


class ImageAnalyzer:
    """
    Describes images or answers questions about them using SmolVLM-Instruct.

    The model is loaded lazily on the first call to describe() so
    importing this module has zero overhead.

    Parameters
    ----------
    model_id   : HuggingFace model ID (default: SmolVLM-Instruct)
    max_tokens : maximum number of tokens in the generated response
    device     : "cpu" (default) or "cuda" if a GPU is available
    """

    def __init__(
        self,
        model_id: str = _DEFAULT_MODEL,
        max_tokens: int = 200,
        device: str = "cpu",
    ):
        self._model_id = model_id
        self._max_tokens = max_tokens
        self._device = device
        self._processor = None
        self._model = None

    # ------------------------------------------------------------------ #
    # Lazy model loading                                                    #
    # ------------------------------------------------------------------ #

    def _ensure_loaded(self) -> None:
        """Load model + processor on first use."""
        if self._model is not None:
            return

        try:
            import torch
            from transformers import AutoProcessor
        except ImportError:
            raise ImageAnalyzerError(
                "Required packages not installed. Run:\n"
                "    pip install transformers torch Pillow"
            )

        # AutoModelForVision2Seq was removed in transformers 5.x.
        # SmolVLM is built on Idefics3 – fall back to that class directly.
        try:
            from transformers import AutoModelForVision2Seq
            model_cls = AutoModelForVision2Seq
        except ImportError:
            try:
                from transformers import Idefics3ForConditionalGeneration
                model_cls = Idefics3ForConditionalGeneration
            except ImportError:
                raise ImageAnalyzerError(
                    "Could not find a suitable model class in your transformers version. "
                    "Try: pip install --upgrade transformers"
                )

        logger.info(f"Loading SmolVLM model '{self._model_id}' on {self._device}...")
        logger.info(f"Using model class: {model_cls.__name__}")
        logger.info("(First run downloads ~2 GB - this takes a moment.)")

        self._processor = AutoProcessor.from_pretrained(self._model_id)
        self._model = model_cls.from_pretrained(
            self._model_id,
            torch_dtype=torch.float32,    # float32 is safest for CPU
            _attn_implementation="eager", # no flash_attention on CPU
        ).to(self._device)
        self._model.eval()

        logger.info("SmolVLM model loaded and ready.")

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    async def warmup(self) -> None:
        """Pre-load the model at startup instead of waiting for the first image.

            analyzer = ImageAnalyzer()
            await analyzer.warmup()   # loads model (~2 GB first time)
            print("Ready!")
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._ensure_loaded)

    async def describe(
        self,
        image_data: bytes,
        prompt: Optional[str] = None,
    ) -> str:
        """
        Describe an image or answer a specific question about it.

        Runs model inference in a thread pool so it doesn't block the
        asyncio event loop.

        Parameters
        ----------
        image_data : raw image bytes (JPEG, PNG, WebP, ...)
        prompt     : optional question or instruction, e.g.:
                       "Is the light on?"
                       "How many people are in the room?"
                       "Describe what you see for a home automation system."
                     Defaults to a general home-automation-focused description.

        Returns
        -------
        A detailed text response from SmolVLM.
        """
        effective_prompt = prompt or _DEFAULT_PROMPT
        logger.info(f"Prompt: {effective_prompt}")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._describe_sync, image_data, effective_prompt
        )

    # ------------------------------------------------------------------ #
    # Internal: synchronous inference                                       #
    # ------------------------------------------------------------------ #

    def _describe_sync(self, image_data: bytes, prompt: str) -> str:
        """Synchronous inference - called from a thread pool by describe()."""
        self._ensure_loaded()

        try:
            import torch

            image = Image.open(io.BytesIO(image_data)).convert("RGB")

            # SmolVLM uses a chat template - we build a user message with
            # one image and the text prompt
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image"},
                        {"type": "text", "text": prompt},
                    ],
                }
            ]

            # Apply chat template to get the full prompt string
            text_prompt = self._processor.apply_chat_template(
                messages,
                add_generation_prompt=True,
            )

            inputs = self._processor(
                text=text_prompt,
                images=[image],
                return_tensors="pt",
            ).to(self._device)

            with torch.no_grad():
                output_ids = self._model.generate(
                    **inputs,
                    max_new_tokens=self._max_tokens,
                    do_sample=False,  # greedy decoding - faster and deterministic on CPU
                )

            # Decode only the newly generated tokens (skip the input prompt)
            generated_ids = output_ids[:, inputs["input_ids"].shape[1]:]
            response = self._processor.batch_decode(
                generated_ids,
                skip_special_tokens=True,
            )[0].strip()

            logger.info(f"SmolVLM response: {response[:120]}...")
            return response

        except Exception as exc:
            logger.error(f"Image analysis failed: {exc}")
            raise ImageAnalyzerError(f"Could not analyze image: {exc}") from exc

    def unload(self) -> None:
        """Release the model from memory."""
        self._model = None
        self._processor = None
        logger.info("SmolVLM model unloaded from memory.")
