"""
matrix_receiver.py – Listen for incoming Matrix messages via an async generator.

All connection and encryption logic lives in MatrixBaseClient.
This class only adds what makes it unique: the sync loop, event callbacks,
and the async generator interface.

Quick-start
-----------
    import asyncio
    from matrix_receiver import MatrixReceiver, MatrixMessage

    async def main():
        async with MatrixReceiver.from_env() as receiver:
            async for msg in receiver.messages():
                print(f"{msg.sender_display}: {msg.body}")
                await receiver.send(msg.room_id, f"Got it!")
                if msg.body == "!stop":
                    break

    asyncio.run(main())

Required .env variables: MATRIX_HOMESERVER, BOT_USER_ID, BOT_PASSWORD, ROOM_ID
Optional .env variables:
    SYNC_TOKEN_PATH  (default: "sync_token.json")
    KNOWN_USER       (if set, only messages from this Matrix ID are yielded)
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Optional

from nio import (
    KeyVerificationEvent,
    MegolmEvent,
    RoomKeyEvent,
    RoomKeyRequest,
    RoomEncryptedImage,   # encrypted image (E2EE rooms) – nio decrypts automatically
    RoomMessageImage,     # unencrypted image (rare, non-E2EE rooms)
    RoomMessageText,
)

from matrix_base import MatrixBaseClient, MatrixClientError  # noqa: F401

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Data model                                                                    #
# --------------------------------------------------------------------------- #

@dataclass
class MatrixMessage:
    """A received Matrix room message.

    Attributes
    ----------
    type      : "text" or "image"
    body      : text content for text messages; filename for image messages
    data      : raw image bytes for image messages; None for text messages
    mime_type : e.g. "image/jpeg" for image messages; None for text
    """
    room_id: str
    event_id: str
    sender: str            # Full Matrix ID, e.g. "@chris:matrix.example.com"
    sender_display: str    # Short name, e.g. "chris"
    body: str
    type: str = "text"
    data: Optional[bytes] = field(default=None, repr=False)
    mime_type: Optional[str] = None


# --------------------------------------------------------------------------- #
# Receiver                                                                      #
# --------------------------------------------------------------------------- #

class MatrixReceiver(MatrixBaseClient):
    """
    Yields incoming room messages as an async generator.

    Extends MatrixBaseClient with:
      • A background sync loop
      • Event callbacks (messages, encryption key handling)
      • Sync token persistence (resumes from last position after restart)

    Extra parameters (compared to MatrixBaseClient)
    ------------------------------------------------
    known_user      : if set, only messages from this Matrix ID are yielded
    sync_token_path : JSON file for persisting the sync position
    """

    def __init__(self, *args, known_user: str = "",
                 sync_token_path: str = "sync_token.json", **kwargs):
        super().__init__(*args, **kwargs)
        self._known_user = known_user
        self._sync_token_path = sync_token_path
        self._queue: asyncio.Queue[MatrixMessage] = asyncio.Queue()
        self._processed_events: set[str] = set()
        self._startup_done = False

    # ------------------------------------------------------------------ #
    # Factory override (adds known_user + sync_token_path)                 #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_env(cls, env_file: str = ".env") -> "MatrixReceiver":
        env = cls._load_env(env_file)
        return cls(
            homeserver=env["MATRIX_HOMESERVER"],
            user_id=env["BOT_USER_ID"],
            password=env["BOT_PASSWORD"],
            room_id=env["ROOM_ID"],
            store_path=env["STORE_PATH"],
            config_path=env["CONFIG_PATH"],
            known_user=env["KNOWN_USER"],
            sync_token_path=env["SYNC_TOKEN_PATH"],
        )

    # ------------------------------------------------------------------ #
    # Connection override (adds callbacks + startup sync)                  #
    # ------------------------------------------------------------------ #

    async def _connect(self) -> None:
        """Connect, register callbacks, and perform the startup sync."""
        self._device_name = "matrix-receiver"

        # Create the client and register callbacks BEFORE calling super()._connect(),
        # because super() runs the initial sync which would trigger callbacks.
        from nio import AsyncClient
        self._client = self._client or None
        # Let super handle login + keys + initial sync
        await super()._connect()

        # Register event callbacks after encryption is ready
        self._register_callbacks()
        self._startup_done = True
        logger.info("MatrixReceiver ready – listening for messages.")

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    async def messages(self) -> AsyncIterator[MatrixMessage]:
        """
        Async generator that yields incoming messages one by one.

        Runs the Matrix sync loop as a background task; received messages
        land in an internal queue and are yielded here in order.

            async for msg in receiver.messages():
                print(msg.sender_display, "→", msg.body)
        """
        if self._client is None:
            raise MatrixClientError("Not connected. Use 'async with MatrixReceiver…'")

        sync_task = asyncio.create_task(self._sync_loop())
        try:
            while True:
                msg = await self._queue.get()
                yield msg
        finally:
            sync_task.cancel()
            try:
                await sync_task
            except asyncio.CancelledError:
                pass

    async def send(self, room_id: str, message: str) -> None:
        """Send a plain-text reply to a room (e.g. to acknowledge a command)."""
        await self._send_to_room(room_id, message)

    # ------------------------------------------------------------------ #
    # Internal: event callbacks                                            #
    # ------------------------------------------------------------------ #

    def _register_callbacks(self) -> None:
        self._client.add_event_callback(self._on_room_message, RoomMessageText)
        self._client.add_event_callback(self._on_image_message, RoomMessageImage)
        self._client.add_event_callback(self._on_image_message, RoomEncryptedImage)  # E2EE rooms
        self._client.add_event_callback(self._on_encrypted_event, MegolmEvent)
        self._client.add_to_device_callback(
            self._on_to_device_event,
            (RoomKeyRequest, RoomKeyEvent, KeyVerificationEvent),
        )

    def _passes_filters(self, room, event) -> bool:
        if not self._startup_done:
            logger.debug(f"FILTER: startup not done, skipping {event.event_id[:10]}")
            return False
        if room.room_id != self._room_id:
            logger.warning(f"FILTER: wrong room — got '{room.room_id}', expected '{self._room_id}'")
            return False
        if event.sender == self._user_id:
            logger.debug(f"FILTER: skipping own message")
            return False
        if event.event_id in self._processed_events:
            logger.debug(f"FILTER: duplicate event {event.event_id[:10]}")
            return False
        if self._known_user and self._known_user not in event.sender:
            logger.warning(f"FILTER: unknown user '{event.sender}', expected '{self._known_user}'")
            return False
        self._processed_events.add(event.event_id)
        return True

    async def _on_room_message(self, room, event: RoomMessageText) -> None:
        logger.info(f"CALLBACK _on_room_message triggered: {event.sender}")
        if not self._passes_filters(room, event):
            return
        msg = MatrixMessage(
            room_id=room.room_id,
            event_id=event.event_id,
            sender=event.sender,
            sender_display=event.sender.split(":")[0].lstrip("@"),
            body=event.body,
            type="text",
        )
        logger.info(f"Text message queued from {msg.sender_display}: {msg.body[:80]}")
        await self._queue.put(msg)

    async def _on_image_message(self, room, event) -> None:
        """Handle both RoomMessageImage (plain) and RoomEncryptedImage (E2EE).

        For encrypted images the Matrix spec stores the AES key, IV and SHA256
        hash inside the event's 'file' field.  client.download() only fetches
        the raw (still-encrypted) bytes; we must call decrypt_attachment()
        ourselves to get the plaintext image.
        """
        logger.info(f"CALLBACK _on_image_message triggered: {event.sender}")
        if not self._passes_filters(room, event):
            return

        content = event.source.get("content", {})
        mime_type: Optional[str] = content.get("info", {}).get("mimetype")
        file_info: Optional[dict] = content.get("file")   # present for E2EE images
        mxc_url: str = (file_info or {}).get("url") or getattr(event, "url", "")

        image_bytes: Optional[bytes] = None
        try:
            resp = await self._client.download(mxc=mxc_url)
            if not (hasattr(resp, "body") and resp.body):
                logger.warning(f"Image download returned no body: {resp}")
            else:
                raw = resp.body
                if file_info:
                    # Encrypted attachment – decrypt using the key info from the event
                    from nio.crypto.attachments import decrypt_attachment
                    # file_info["key"] is a JWK object – decrypt_attachment
                    # expects only the raw key string from the "k" field.
                    image_bytes = decrypt_attachment(
                        ciphertext=raw,
                        key=file_info["key"]["k"],
                        hash=file_info["hashes"]["sha256"],
                        iv=file_info["iv"],
                    )
                    logger.info(f"Image decrypted: {len(image_bytes)} bytes")
                else:
                    # Unencrypted attachment (non-E2EE room)
                    image_bytes = raw
                    logger.info(f"Image downloaded (plain): {len(image_bytes)} bytes")
        except Exception as exc:
            logger.error(f"Failed to download/decrypt image: {exc}")

        msg = MatrixMessage(
            room_id=room.room_id,
            event_id=event.event_id,
            sender=event.sender,
            sender_display=event.sender.split(":")[0].lstrip("@"),
            body=getattr(event, "body", "image"),
            type="image",
            data=image_bytes,
            mime_type=mime_type,
        )
        logger.info(
            f"Image queued from {msg.sender_display} "
            f"(type={type(event).__name__}, mime={mime_type}, "
            f"size={len(image_bytes or b'')} bytes)"
        )
        await self._queue.put(msg)

    async def _on_encrypted_event(self, room, event: MegolmEvent) -> None:
        if not (hasattr(event, "session_id") and event.session_id):
            return
        logger.info(f"Requesting missing session key: {event.session_id[:20]}…")
        try:
            await self._client.room_send_key_request(
                room.room_id, event.session_id, event.algorithm
            )
        except Exception as exc:
            logger.error(f"Failed to send key request: {exc}")

    async def _on_to_device_event(self, event) -> None:
        if isinstance(event, RoomKeyRequest):
            await self._client.continue_key_share(event)
        elif isinstance(event, KeyVerificationEvent):
            await self._client.accept_key_verification(event)

    # ------------------------------------------------------------------ #
    # Internal: sync loop                                                  #
    # ------------------------------------------------------------------ #

    async def _sync_loop(self) -> None:
        """Background task – keeps polling the homeserver for new events."""
        next_batch = self._load_sync_token()
        while True:
            try:
                resp = await self._client.sync(
                    timeout=30_000, full_state=False, since=next_batch
                )
                if hasattr(resp, "next_batch"):
                    next_batch = resp.next_batch
                    self._save_sync_token(next_batch)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Sync error – retrying in 5 s…")
                await asyncio.sleep(5)

    # ------------------------------------------------------------------ #
    # Internal: sync token persistence                                     #
    # ------------------------------------------------------------------ #

    def _load_sync_token(self) -> Optional[str]:
        if not os.path.exists(self._sync_token_path):
            return None
        with open(self._sync_token_path) as f:
            return json.load(f).get("next_batch")

    def _save_sync_token(self, token: str) -> None:
        with open(self._sync_token_path, "w") as f:
            json.dump({"next_batch": token}, f)
