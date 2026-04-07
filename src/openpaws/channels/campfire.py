"""Campfire channel adapter using webhooks.

This adapter connects to a self-hosted Campfire instance (Basecamp ONCE product)
using the webhook-based bot API. It runs a local HTTP server to receive incoming
messages and uses the Campfire API to send responses.

Configuration:
    channels:
      campfire:
        base_url: ${CAMPFIRE_URL}       # https://chat.example.com
        bot_key: ${CAMPFIRE_BOT_KEY}    # 123-abc123xyz456
        webhook_port: 8765              # Local port for webhook server
        webhook_path: /webhook          # Path for webhook endpoint

See docs/CAMPFIRE_SETUP.md for detailed setup instructions.
"""

import asyncio
import logging
import re
from dataclasses import dataclass

from aiohttp import ClientSession, web

from openpaws.channels.base import (
    ChannelAdapter,
    IncomingMessage,
    MessageHandler,
    OutgoingMessage,
)

logger = logging.getLogger(__name__)

# Regex to extract room_id from path like "/rooms/1/botkey/messages"
ROOM_PATH_PATTERN = re.compile(r"/rooms/(\d+)/")


@dataclass
class CampfireConfig:
    """Configuration for Campfire adapter.

    Attributes:
        base_url: Base URL of the Campfire instance (e.g., https://chat.example.com)
        bot_key: Bot authentication key in format {id}-{token}
        webhook_port: Local port to listen for webhook callbacks
        webhook_path: URL path for the webhook endpoint
    """

    base_url: str
    bot_key: str
    webhook_port: int = 8765
    webhook_path: str = "/webhook"

    def __post_init__(self):
        self._validate_base_url()
        self._validate_bot_key()
        self._validate_webhook_port()

    def _validate_base_url(self) -> None:
        if not self.base_url:
            raise ValueError("Campfire base_url is required")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError(
                f"base_url must start with http:// or https://: {self.base_url}"
            )

    def _validate_bot_key(self) -> None:
        if not self.bot_key:
            raise ValueError("Campfire bot_key is required")
        if "-" not in self.bot_key:
            raise ValueError(
                "bot_key must be in format {id}-{token}. "
                "Get it from Campfire admin > Account > Bots"
            )

    def _validate_webhook_port(self) -> None:
        if not 1 <= self.webhook_port <= 65535:
            raise ValueError(f"webhook_port must be 1-65535: {self.webhook_port}")


class CampfireAdapter(ChannelAdapter):
    """Campfire channel adapter using webhooks for message reception."""

    def __init__(self, config: CampfireConfig):
        self._config = config
        self._message_handler: MessageHandler | None = None
        self._running = False
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._http_session: ClientSession | None = None

    @property
    def channel_type(self) -> str:
        return "campfire"

    def _extract_room_id(self, room_path: str) -> str:
        """Extract room ID from the room path."""
        match = ROOM_PATH_PATTERN.search(room_path)
        if match:
            return match.group(1)
        return ""

    def _create_incoming_message(self, payload: dict) -> IncomingMessage:
        """Convert Campfire webhook payload to IncomingMessage."""
        user = payload.get("user", {})
        room = payload.get("room", {})
        message = payload.get("message", {})
        body = message.get("body", {})

        room_path = room.get("path", "")
        room_id = self._extract_room_id(room_path) or str(room.get("id", ""))

        return IncomingMessage(
            channel_type=self.channel_type,
            channel_id=room_id,
            user_id=str(user.get("id", "")),
            user_name=user.get("name", ""),
            text=body.get("plain", ""),
            thread_id=str(message.get("id", "")),
            is_mention=True,
            is_dm=False,
            raw_event={
                "user": user,
                "room": room,
                "message": message,
                "room_path": room_path,
            },
        )

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """Handle incoming webhook POST from Campfire."""
        if self._message_handler is None:
            return web.Response(status=503, text="Handler not ready")

        try:
            payload = await request.json()
        except Exception as e:
            logger.error(f"Failed to parse webhook payload: {e}")
            return web.Response(status=400, text="Invalid JSON")

        incoming = self._create_incoming_message(payload)
        logger.info(
            f"Received message from {incoming.user_name} in room {incoming.channel_id}"
        )

        try:
            response = await self._message_handler(incoming)
        except Exception as e:
            logger.error(f"Message handler error: {e}")
            return web.Response(status=500, text="Handler error")

        if response:
            return web.Response(text=response, content_type="text/plain")

        return web.Response(status=204)

    async def _health_check(self, request: web.Request) -> web.Response:
        """Simple health check endpoint."""
        return web.Response(text="OK")

    def _setup_routes(self) -> web.Application:
        """Create and configure the web application."""
        app = web.Application()
        app.router.add_post(self._config.webhook_path, self._handle_webhook)
        app.router.add_get("/health", self._health_check)
        return app

    async def start(self, message_handler: MessageHandler) -> None:
        """Start the Campfire adapter with webhook server."""
        if self._running:
            logger.warning("Campfire adapter already running")
            return

        self._message_handler = message_handler
        self._http_session = ClientSession()

        self._app = self._setup_routes()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, "0.0.0.0", self._config.webhook_port)
        await self._site.start()

        self._running = True
        logger.info(
            f"Campfire adapter started - webhook listening on "
            f"http://0.0.0.0:{self._config.webhook_port}{self._config.webhook_path}"
        )

    async def stop(self) -> None:
        """Stop the Campfire adapter."""
        if not self._running:
            return

        logger.info("Stopping Campfire adapter")
        self._running = False

        if self._site:
            await self._site.stop()
            self._site = None

        if self._runner:
            await self._runner.cleanup()
            self._runner = None

        if self._http_session:
            await self._http_session.close()
            self._http_session = None

        self._app = None
        self._message_handler = None

    def _build_message_url(self, room_id: str) -> str:
        """Build URL for posting messages to a room."""
        base = self._config.base_url.rstrip("/")
        return f"{base}/rooms/{room_id}/{self._config.bot_key}/messages"

    async def send_message(self, message: OutgoingMessage) -> None:
        """Send a message to a Campfire room."""
        if not self._http_session:
            raise RuntimeError("Campfire adapter not started")

        url = self._build_message_url(message.channel_id)

        async with self._http_session.post(
            url,
            data=message.text,
            headers={"Content-Type": "text/plain; charset=utf-8"},
        ) as resp:
            if resp.status == 201:
                location = resp.headers.get("Location", "")
                logger.info(f"Sent message to room {message.channel_id}: {location}")
            else:
                body = await resp.text()
                logger.error(f"Failed to send message: {resp.status} - {body}")
                raise RuntimeError(f"Campfire API error: {resp.status}")

    def is_running(self) -> bool:
        return self._running


def create_campfire_adapter(
    base_url: str,
    bot_key: str,
    webhook_port: int = 8765,
    webhook_path: str = "/webhook",
) -> CampfireAdapter:
    """Create a Campfire adapter with the given configuration.

    Args:
        base_url: Base URL of the Campfire instance
        bot_key: Bot authentication key
        webhook_port: Local port for webhook server
        webhook_path: URL path for webhook endpoint

    Returns:
        Configured CampfireAdapter instance
    """
    config = CampfireConfig(
        base_url=base_url,
        bot_key=bot_key,
        webhook_port=webhook_port,
        webhook_path=webhook_path,
    )
    return CampfireAdapter(config)


async def run_campfire_adapter_standalone(
    base_url: str,
    bot_key: str,
    webhook_port: int = 8765,
) -> None:
    """Run Campfire adapter standalone for testing.

    This function runs the Campfire adapter independently, echoing all
    incoming messages. Useful for verifying webhook configuration.

    Args:
        base_url: Base URL of the Campfire instance
        bot_key: Bot authentication key
        webhook_port: Local port for webhook server
    """

    async def echo_handler(msg: IncomingMessage) -> str | None:
        logger.info(f"Message from {msg.user_name}: {msg.text}")
        return f"Echo: {msg.text}"

    adapter = create_campfire_adapter(base_url, bot_key, webhook_port)

    try:
        await adapter.start(echo_handler)
        while adapter.is_running():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down")
    finally:
        await adapter.stop()
