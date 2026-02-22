# MatrixPyBot

A lightweight Python library for sending and receiving messages on a self-hosted [Matrix](https://matrix.org/) server — designed for **home automation**, personal bots, and AI-powered workflows.

Built with [`matrix-nio`](https://github.com/poljar/matrix-nio) and full **end-to-end encryption** support out of the box.

---

## Features

- 📤 **Send messages** to a Matrix room with a single function call
- 📥 **Receive messages** via a clean `async for` generator interface
- 🖼️ **Analyze images** sent to the room using [SmolVLM](https://huggingface.co/HuggingFaceTB/SmolVLM-Instruct) — runs locally on CPU, no cloud needed
- 🔐 **E2EE support** — handles encrypted rooms transparently
- ⚙️ **Simple configuration** via a single `.env` file
- 🏠 **Home automation ready** — import and use in any script with minimal setup

---

## Project Structure

```
MatrixPyBot/
├── matrix_base.py          # Shared base class (login, encryption, send)
├── matrix_notifier.py      # Send messages to a Matrix room
├── matrix_receiver.py      # Receive messages via async generator
├── image_analyzer.py       # Describe images using SmolVLM (local AI)
│
├── test_notifier.py        # Quick test for the notifier
├── test_receiver.py        # Quick test for the receiver
├── test_image_analyzer.py  # Test image analysis via Matrix
│
├── .env.template           # Copy this to .env and fill in your values
└── pyproject.toml
```

---

## Requirements

- Python 3.10+
- A self-hosted or public Matrix homeserver (e.g. [Synapse](https://github.com/element-hq/synapse))
- A **dedicated bot account** on your Matrix server
- The bot must be **invited to the room** you want to use

Install dependencies:

```bash
git clone https://github.com/yourname/MatrixPyBot
cd MatrixPyBot
uv sync
```

All dependencies are declared in `pyproject.toml` — no manual `pip install` needed.

> **Note:** The SmolVLM model (~2 GB) is downloaded automatically from HuggingFace on first use and cached locally.

---

## Configuration

Copy the template and fill in your values:

```bash
cp .env.template .env
```

```env
# Your Matrix homeserver URL
MATRIX_HOMESERVER="https://matrix.yourdomain.com"

# The bot account credentials
BOT_USER_ID="@yourbot:yourdomain.com"
BOT_PASSWORD="your-bot-password"

# The room the bot should operate in (find this in Element: Room Settings → Advanced)
ROOM_ID="!roomid:yourdomain.com"

# Optional: only respond to messages from this user
KNOWN_USER="@you:yourdomain.com"

# File paths (defaults work fine, no need to change)
STORE_PATH=./store
CONFIG_PATH=./bot_credentials.json
SYNC_TOKEN_PATH=./sync_token.json
```

---

## Usage

### Send a message (Notifier)

```python
import asyncio
from matrix_notifier import MatrixNotifier

# Option A: context manager — efficient for multiple messages
async def main():
    async with MatrixNotifier.from_env() as notifier:
        await notifier.send("Living-room light is ON 💡")
        await notifier.send("Temperature: 21 °C 🌡")

asyncio.run(main())

# Option B: one-liner — great for single notifications from any script
asyncio.run(MatrixNotifier.send_once("Motion detected! 🚨"))
```

### Receive messages (Receiver)

```python
import asyncio
from matrix_receiver import MatrixReceiver

async def main():
    async with MatrixReceiver.from_env() as receiver:
        async for msg in receiver.messages():
            print(f"{msg.sender_display}: {msg.body}")

            if msg.body == "!stop":
                await receiver.send(msg.room_id, "Shutting down.")
                break

            await receiver.send(msg.room_id, f"Got it: {msg.body}")

asyncio.run(main())
```

### Receive and handle images

```python
import asyncio
from matrix_receiver import MatrixReceiver
from image_analyzer import ImageAnalyzer

async def main():
    analyzer = ImageAnalyzer()
    await analyzer.warmup()  # pre-load model at startup

    async with MatrixReceiver.from_env() as receiver:
        async for msg in receiver.messages():

            if msg.type == "text":
                await receiver.send(msg.room_id, f"Received: {msg.body}")

            elif msg.type == "image":
                await receiver.send(msg.room_id, "🔍 Analyzing image...")
                description = await analyzer.describe(msg.data)
                await receiver.send(msg.room_id, f"🖼️ {description}")

asyncio.run(main())
```

### Ask a specific question about an image

```python
# Default: general home-automation description
description = await analyzer.describe(image_bytes)

# Targeted question
answer = await analyzer.describe(image_bytes, prompt="Is the light on?")
answer = await analyzer.describe(image_bytes, prompt="How many people are in the room?")
```

### MatrixMessage fields

Every message yielded by `receiver.messages()` is a `MatrixMessage` dataclass:

| Field | Example | Description |
|---|---|---|
| `type` | `"text"` / `"image"` | Message type |
| `body` | `"Hello!"` | Text content or image filename |
| `sender` | `"@user:matrix.example.com"` | Full Matrix user ID |
| `sender_display` | `"user"` | Short display name |
| `room_id` | `"!abc:matrix.example.com"` | Room the message came from |
| `event_id` | `"$xyz..."` | Unique Matrix event ID |
| `data` | `bytes` | Raw image bytes (images only) |
| `mime_type` | `"image/jpeg"` | MIME type (images only) |

---

## Home Automation Example

A typical pattern: one script sends sensor data, another listens for commands from your phone.

```python
# sensor_script.py — runs on a schedule (e.g. cron)
import asyncio
from matrix_notifier import MatrixNotifier

lux = read_light_sensor()
asyncio.run(MatrixNotifier.send_once(f"Living room: {lux} lux 💡"))
```

```python
# command_listener.py — runs continuously
import asyncio
from matrix_receiver import MatrixReceiver

async def main():
    async with MatrixReceiver.from_env() as receiver:
        async for msg in receiver.messages():
            if msg.body == "lights on":
                turn_on_lights()
                await receiver.send(msg.room_id, "💡 Lights on!")
            elif msg.body == "lights off":
                turn_off_lights()
                await receiver.send(msg.room_id, "🌑 Lights off!")

asyncio.run(main())
```

---

## First Run Notes

On the very first run, MatrixPyBot will:
1. Log in to your homeserver and cache the session in `bot_credentials.json`
2. Create a crypto store in `store/` for E2EE keys
3. Download the SmolVLM model (~2 GB) to `~/.cache/huggingface/hub/` (image analysis only)

Subsequent runs are fast — the session and model are cached.

> **Important:** Make sure `bot_credentials.json`, `sync_token.json` and `store/` are in your `.gitignore`. They contain your bot's access token and encryption keys and must never be committed to version control. The provided `.gitignore` already handles this.

---

## License

MIT — do whatever you want with it.