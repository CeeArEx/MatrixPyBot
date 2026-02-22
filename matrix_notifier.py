"""
matrix_notifier.py – Send plain-text messages to a Matrix room.

Designed for one-shot use from any script (e.g. home-automation jobs).
All connection and encryption logic lives in MatrixBaseClient.

Quick-start
-----------
    import asyncio
    from matrix_notifier import MatrixNotifier

    # Option A: context manager (multiple messages, one connection)
    async def main():
        async with MatrixNotifier.from_env() as notifier:
            await notifier.send("Living-room light is ON 💡")
            await notifier.send("Temperature: 21 °C 🌡")

    asyncio.run(main())

    # Option B: one-liner (single message, auto connect/disconnect)
    asyncio.run(MatrixNotifier.send_once("Alarm triggered! 🚨"))

Required .env variables: MATRIX_HOMESERVER, BOT_USER_ID, BOT_PASSWORD, ROOM_ID
"""

from matrix_base import MatrixBaseClient, MatrixClientError  # noqa: F401


class MatrixNotifier(MatrixBaseClient):
    """Sends messages to the room configured in .env (ROOM_ID)."""

    async def _connect(self) -> None:
        self._device_name = "matrix-notifier"
        await super()._connect()

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    async def send(self, message: str) -> None:
        """Send a plain-text message to the configured room."""
        await self._send_to_room(self._room_id, message)

    @classmethod
    async def send_once(cls, message: str, env_file: str = ".env") -> None:
        """Connect, send one message, and disconnect – all in one call.

            await MatrixNotifier.send_once("Alarm! 🚨")
        """
        async with cls.from_env(env_file) as notifier:
            await notifier.send(message)
