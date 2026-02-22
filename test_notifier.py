"""
test_notifier.py – Schnelltest für MatrixNotifier.

Lege diese Datei in denselben Ordner wie deine .env und matrix_notifier.py,
dann einfach ausführen:

    python test_notifier.py
"""

import asyncio
from matrix_notifier import MatrixNotifier
import logging

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)-8s] %(filename)s:%(lineno)d – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# --- Beispielwerte ---
lux = 342
raum = "Wohnzimmer"
status = "an"


async def main():
    print("🔌 Verbinde mit Matrix-Server...")

    # Option 1: Context Manager – gut wenn du mehrere Nachrichten senden willst
    async with MatrixNotifier.from_env() as notifier:
        await notifier.send(f"💡 {raum}: Licht ist {status} ({lux} lux)")
        await notifier.send("✅ Test erfolgreich – Verbindung funktioniert!")

    print("✓ Nachrichten gesendet – schau auf deinen Matrix-Server!")

    # Option 2: One-liner – gut für einzelne Benachrichtigungen aus einem Skript
    await MatrixNotifier.send_once("🚨 Alarm ausgelöst!")


if __name__ == "__main__":
    asyncio.run(main())
