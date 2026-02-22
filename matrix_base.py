"""
matrix_base.py – Shared base class for MatrixNotifier and MatrixReceiver.

Handles everything that both classes need:
  • .env loading & validation
  • Login with credential caching
  • Encryption key bootstrap (upload → sync → verify)
  • Sending plain-text messages to a room
  • Graceful disconnect

Not intended to be used directly – use MatrixNotifier or MatrixReceiver.
"""

import asyncio
import json
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from nio import AsyncClient, LoginResponse, RoomSendResponse

logger = logging.getLogger(__name__)


class MatrixClientError(Exception):
    """Raised when a Matrix client cannot connect, login, or send."""


class MatrixBaseClient:
    """
    Shared foundation for all Matrix clients in this project.

    Subclasses get login, encryption, and send for free.
    They only need to implement what makes them unique.

    Parameters
    ----------
    homeserver  : e.g. "https://matrix.example.com"
    user_id     : e.g. "@bot:example.com"
    password    : bot account password
    room_id     : default room for send()
    store_path  : directory for the nio crypto store
    config_path : JSON file used to cache the access token between runs
    device_name : displayed in the Matrix device list
    """

    def __init__(
        self,
        homeserver: str,
        user_id: str,
        password: str,
        room_id: str,
        store_path: str = "store",
        config_path: str = "bot_credentials.json",
        device_name: str = "matrix-client",
    ):
        self._homeserver = homeserver
        self._user_id = user_id
        self._password = password
        self._room_id = room_id
        self._store_path = store_path
        self._config_path = config_path
        self._device_name = device_name
        self._client: Optional[AsyncClient] = None

        os.makedirs(store_path, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Factory                                                               #
    # ------------------------------------------------------------------ #

    # Required .env keys – subclasses can extend this list
    _REQUIRED_ENV_KEYS = ("MATRIX_HOMESERVER", "BOT_USER_ID", "BOT_PASSWORD", "ROOM_ID")

    @classmethod
    def _load_env(cls, env_file: str) -> dict:
        """Load and validate environment variables. Returns a dict of all values."""
        load_dotenv(env_file)

        values = {
            "MATRIX_HOMESERVER": os.getenv("MATRIX_HOMESERVER", ""),
            "BOT_USER_ID":       os.getenv("BOT_USER_ID", ""),
            "BOT_PASSWORD":      os.getenv("BOT_PASSWORD", ""),
            "ROOM_ID":           os.getenv("ROOM_ID", ""),
            "STORE_PATH":        os.getenv("STORE_PATH", "store"),
            "CONFIG_PATH":       os.getenv("CONFIG_PATH", "bot_credentials.json"),
            "SYNC_TOKEN_PATH":   os.getenv("SYNC_TOKEN_PATH", "sync_token.json"),
            "KNOWN_USER":        os.getenv("KNOWN_USER", ""),
        }

        missing = [k for k in cls._REQUIRED_ENV_KEYS if not values.get(k)]
        if missing:
            raise MatrixClientError(
                f"Missing required environment variables: {', '.join(missing)}"
            )
        return values

    @classmethod
    def from_env(cls, env_file: str = ".env") -> "MatrixBaseClient":
        """Create an instance from environment variables / .env file.

        Works correctly on subclasses:
            MatrixNotifier.from_env()  → MatrixNotifier instance
            MatrixReceiver.from_env()  → MatrixReceiver instance
        """
        env = cls._load_env(env_file)
        return cls(
            homeserver=env["MATRIX_HOMESERVER"],
            user_id=env["BOT_USER_ID"],
            password=env["BOT_PASSWORD"],
            room_id=env["ROOM_ID"],
            store_path=env["STORE_PATH"],
            config_path=env["CONFIG_PATH"],
        )

    # ------------------------------------------------------------------ #
    # Context manager                                                       #
    # ------------------------------------------------------------------ #

    async def __aenter__(self) -> "MatrixBaseClient":
        await self._connect()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    async def close(self) -> None:
        """Disconnect from the Matrix homeserver."""
        if self._client:
            await self._client.close()
            self._client = None
            logger.info(f"{self.__class__.__name__} disconnected.")

    # ------------------------------------------------------------------ #
    # Public: send                                                          #
    # ------------------------------------------------------------------ #

    async def _send_to_room(self, room_id: str, message: str) -> None:
        """Send a plain-text message to any room. Used by subclasses."""
        if self._client is None:
            raise MatrixClientError(
                f"Not connected. Use 'async with {self.__class__.__name__}…'"
            )
        await asyncio.sleep(0.25)
        await self._client.share_group_session(room_id)
        resp = await self._client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": message},
        )
        if isinstance(resp, RoomSendResponse):
            logger.info(f"Message sent (event_id={resp.event_id})")
        else:
            logger.warning(f"Unexpected room_send response: {resp}")

    # ------------------------------------------------------------------ #
    # Internal: connection                                                  #
    # ------------------------------------------------------------------ #

    async def _connect(self) -> None:
        """Create the nio client, log in, and bootstrap encryption."""
        self._client = AsyncClient(
            self._homeserver,
            self._user_id,
            store_path=self._store_path,
        )

        if not await self._login():
            raise MatrixClientError("Login to Matrix homeserver failed.")

        await self._upload_keys_if_needed()

        # Sync first – populates room membership & device store
        logger.info("Initial sync…")
        await self._client.sync(timeout=10_000)

        await self._query_and_verify_all_devices()
        logger.info(f"{self.__class__.__name__} connected and ready.")

    # ------------------------------------------------------------------ #
    # Internal: login                                                       #
    # ------------------------------------------------------------------ #

    async def _login(self) -> bool:
        """Restore a cached session or fall back to a fresh password login."""
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path) as f:
                    creds = json.load(f)
                self._client.restore_login(
                    user_id=self._user_id,
                    device_id=creds["device_id"],
                    access_token=creds["access_token"],
                )
                self._client.load_store()
                logger.info("Session restored from cached credentials.")
                return True
            except Exception as exc:
                logger.warning(f"Could not restore session ({exc}) – fresh login.")

        return await self._fresh_login()

    async def _fresh_login(self) -> bool:
        """Perform a password login and persist the resulting credentials."""
        resp = await self._client.login(self._password, device_name=self._device_name)
        if not isinstance(resp, LoginResponse):
            logger.error(f"Login failed: {resp}")
            return False
        with open(self._config_path, "w") as f:
            json.dump({"access_token": resp.access_token, "device_id": resp.device_id}, f)
        self._client.load_store()
        logger.info("Fresh login successful, credentials cached.")
        return True

    # ------------------------------------------------------------------ #
    # Internal: encryption                                                  #
    # ------------------------------------------------------------------ #

    async def _upload_keys_if_needed(self) -> None:
        if not self._client.should_upload_keys:
            return
        try:
            await self._client.keys_upload()
            logger.info("Encryption keys uploaded.")
        except Exception as exc:
            logger.error(f"Failed to upload keys: {exc}")

    async def _query_and_verify_all_devices(self) -> None:
        """Query the server for all device keys and mark every device as trusted.

        Every device in the room must be verified (or blacklisted) before
        nio's share_group_session() will encrypt for it.
        """
        try:
            await self._client.keys_query()
            logger.info("Device keys queried from server.")
        except Exception as exc:
            logger.error(f"Failed to query device keys: {exc}")

        verified = 0
        for uid in self._client.device_store.users:
            for device in self._client.device_store[uid].values():
                try:
                    self._client.verify_device(device)
                    verified += 1
                    logger.debug(f"Verified device {device.device_id} for {uid}")
                except Exception as exc:
                    logger.error(f"Could not verify device {device.device_id} ({uid}): {exc}")
        logger.info(f"Verified {verified} device(s) in total.")
