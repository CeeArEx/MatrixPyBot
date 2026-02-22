"""
test_image_analyzer.py – Teste den ImageAnalyzer mit dem MatrixReceiver.

Ausführen:
    python test_image_analyzer.py

Das SmolVLM-Modell wird beim Start vorgeladen (~2 GB beim ersten Download).
"""

import asyncio
import logging
from image_analyzer import ImageAnalyzer
from matrix_receiver import MatrixReceiver

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)-8s] %(filename)s:%(lineno)d – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def main():
    # ------------------------------------------------------------------ #
    # Schritt 1: Modell vorladen                                           #
    # ------------------------------------------------------------------ #
    print("🧠 Lade SmolVLM-Modell (beim ersten Mal ~2 GB Download)...")
    analyzer = ImageAnalyzer()
    await analyzer.warmup()
    print("✅ Modell bereit.\n")

    # ------------------------------------------------------------------ #
    # Schritt 2: Matrix-Verbindung aufbauen                               #
    # ------------------------------------------------------------------ #
    print("🔌 Verbinde mit Matrix-Server...")
    async with MatrixReceiver.from_env() as receiver:
        print("✅ Verbunden! Schick ein Bild oder Text aus Element.\n")
        print("   Text-Befehle:")
        print("     !stop              – beendet das Skript")
        print("     !frage <text>      – nächstes Bild mit dieser Frage analysieren\n")

        pending_prompt = None   # wird gesetzt wenn der User !frage schickt

        async for msg in receiver.messages():

            if msg.type == "text":
                print(f"💬 [{msg.sender_display}]: {msg.body}")
                cmd = msg.body.strip()

                if cmd == "!stop":
                    await receiver.send(msg.room_id, "👋 Werde beendet.")
                    break

                elif cmd.startswith("!frage "):
                    pending_prompt = cmd[7:].strip()
                    await receiver.send(
                        msg.room_id,
                        f"✓ Frage gespeichert: \"{pending_prompt}\"\n"
                        f"Schick jetzt ein Bild!"
                    )

                else:
                    await receiver.send(msg.room_id, f"✓ Erhalten: '{msg.body}'")

            elif msg.type == "image":
                print(f"🖼️  [{msg.sender_display}] hat ein Bild gesendet ({msg.mime_type})")

                if msg.data is None:
                    await receiver.send(msg.room_id, "❌ Bild konnte nicht heruntergeladen werden.")
                    continue

                # Sofort Feedback – SmolVLM braucht auf CPU 15–30 Sek.
                if pending_prompt:
                    await receiver.send(msg.room_id, f"🔍 Analysiere: \"{pending_prompt}\"...")
                else:
                    await receiver.send(msg.room_id, "🔍 Beschreibe Bild...")

                try:
                    # Entweder mit gespeicherter Frage oder Standard-Beschreibung
                    response = await analyzer.describe(msg.data, prompt=pending_prompt)
                    pending_prompt = None   # Frage nach Verwendung zurücksetzen

                    print(f"   → SmolVLM: {response}")
                    await receiver.send(msg.room_id, f"🖼️ {response}")

                except Exception as e:
                    print(f"   → Fehler: {e}")
                    await receiver.send(msg.room_id, f"❌ Analyse fehlgeschlagen: {e}")
                    pending_prompt = None


if __name__ == "__main__":
    asyncio.run(main())
