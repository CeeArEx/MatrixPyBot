"""
test_receiver.py – Teste den MatrixReceiver.

Leg diese Datei in denselben Ordner wie matrix_receiver.py und deine .env,
dann ausführen:

    python test_receiver.py

Schreib dann eine Nachricht aus Element / deinem Handy → sie erscheint hier.
Schreib "!stop" um das Skript sauber zu beenden.
"""

import asyncio
import logging
from matrix_receiver import MatrixReceiver, MatrixMessage

# Optionales Logging – zeigt was intern passiert
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)-8s] %(filename)s:%(lineno)d – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def main():
    print("🔌 Verbinde mit Matrix-Server...")

    async with MatrixReceiver.from_env() as receiver:
        print("✅ Bereit! Schreib eine Nachricht in Element oder auf deinem Handy.")
        print("   Tippe '!stop' um das Skript zu beenden.\n")

        async for msg in receiver.messages():
            # --- Deine Verarbeitungslogik kommt hier rein ---

            print(f"📨 [{msg.sender_display}]: {msg.body}")

            # Beispiel 1: Einfach bestätigen
            await receiver.send(msg.room_id, f"✓ Erhalten: '{msg.body}'")

            # Beispiel 2: Einfaches Command-Handling
            command = msg.body.strip().lower()

            if command == "!stop":
                await receiver.send(msg.room_id, "👋 Receiver wird beendet.")
                print("Stop-Befehl empfangen – beende.")
                break

            elif command == "!status":
                await receiver.send(msg.room_id, "🟢 Receiver läuft!")

            elif command.startswith("!echo "):
                text = msg.body[6:]
                await receiver.send(msg.room_id, f"🔁 {text}")

            # Beispiel 3: Strukturierter Zugriff auf die Nachricht
            # msg.sender         → "@chris:matrix.example.com"
            # msg.sender_display → "chris"
            # msg.body           → "Hallo!"
            # msg.room_id        → "!abc123:matrix.example.com"
            # msg.event_id       → "$xyz..."


# -----------------------------------------------------------------------
# Praxisbeispiel Home Automation:
# So würde ein echtes Licht-Steuerungs-Skript aussehen
# -----------------------------------------------------------------------
async def home_automation_example():
    """
    Empfange Befehle vom Handy und steuere damit Geräte.
    Kombiniert Receiver (eingehend) + Notifier (ausgehend).
    """
    from matrix_notifier import MatrixNotifier

    async with MatrixReceiver.from_env() as receiver:
        async for msg in receiver.messages():
            cmd = msg.body.strip().lower()
            print(f"Befehl von {msg.sender_display}: {cmd}")

            if cmd == "licht an":
                # → hier deine GPIO / Home-Assistant / API Logik
                # turn_on_light()
                await receiver.send(msg.room_id, "💡 Licht eingeschaltet!")

            elif cmd == "licht aus":
                # turn_off_light()
                await receiver.send(msg.room_id, "🌑 Licht ausgeschaltet!")

            elif cmd == "status":
                # lux = read_sensor()
                lux = 342  # Beispielwert
                await receiver.send(msg.room_id, f"📊 Aktuell: {lux} lux")

            elif cmd == "!stop":
                break


if __name__ == "__main__":
    asyncio.run(main())
    # Zum Testen des Home-Automation-Beispiels:
    # asyncio.run(home_automation_example())
