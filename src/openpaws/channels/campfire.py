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
        context_messages: 10            # Number of recent messages for context

See docs/CAMPFIRE_SETUP.md for detailed setup instructions.

Note: Reading conversation context requires the bot read API (PR #190).
      Use the jpshackelford/once-campfire:bot-read-messages image or wait
      for the PR to be merged upstream.
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

# Default number of recent messages to include for context
DEFAULT_CONTEXT_MESSAGES = 10


@dataclass
class CampfireConfig:
    """Configuration for Campfire adapter.

    Attributes:
        base_url: Base URL of the Campfire instance (e.g., https://chat.example.com)
        bot_key: Bot authentication key in format {id}-{token}
        webhook_port: Local port to listen for webhook callbacks
        webhook_path: URL path for the webhook endpoint
        context_messages: Number of recent messages to fetch for context (0 to disable)
    """

    base_url: str
    bot_key: str
    webhook_port: int = 8765
    webhook_path: str = "/webhook"
    context_messages: int = DEFAULT_CONTEXT_MESSAGES

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
        message_id = str(message.get("id", ""))

        # Create callbacks for status updates
        async def on_processing_start() -> None:
            """Add 👀 reaction when handler starts processing."""
            await self.add_reaction(room_id, message_id, "👀")

        async def send_status(text: str) -> None:
            """Send an interim status message."""
            outgoing = OutgoingMessage(
                channel_id=room_id,
                text=text,
                thread_id=message_id,
            )
            await self.send_message(outgoing)

        return IncomingMessage(
            channel_type=self.channel_type,
            channel_id=room_id,
            user_id=str(user.get("id", "")),
            user_name=user.get("name", ""),
            text=body.get("plain", ""),
            thread_id=message_id,
            is_mention=True,
            is_dm=False,
            raw_event={
                "user": user,
                "room": room,
                "message": message,
                "room_path": room_path,
            },
            on_processing_start=on_processing_start,
            send_status=send_status,
        )

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """Handle incoming webhook POST from Campfire.

        Acknowledges immediately and processes the message in the background.
        Response is sent back to Campfire via the API when ready.
        """
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

        # Process message in background - don't block the webhook response
        asyncio.create_task(self._process_message_async(incoming))

        # Acknowledge receipt immediately (204 No Content - no body for Campfire)
        return web.Response(status=204)

    async def _process_message_async(self, incoming: IncomingMessage) -> None:
        """Process message asynchronously and send response back to Campfire.

        Fetches conversation context from the room and prepends it to the message
        so the agent can understand the conversation flow.
        """
        try:
            # Fetch conversation context (messages before the current one)
            context_messages = await self.fetch_room_context(
                incoming.channel_id, before_message_id=incoming.thread_id
            )

            # Build the full message with context
            if context_messages:
                context_text = self._format_context_for_prompt(
                    context_messages, incoming.user_name
                )
                full_message = f"{context_text}{incoming.text}"
                # Create a new IncomingMessage with the contextualized text
                # Important: Copy the callbacks from the original message!
                incoming_with_context = IncomingMessage(
                    channel_type=incoming.channel_type,
                    channel_id=incoming.channel_id,
                    user_id=incoming.user_id,
                    user_name=incoming.user_name,
                    text=full_message,
                    thread_id=incoming.thread_id,
                    is_mention=incoming.is_mention,
                    is_dm=incoming.is_dm,
                    raw_event=incoming.raw_event,
                    on_processing_start=incoming.on_processing_start,
                    send_status=incoming.send_status,
                )
                response = await self._message_handler(incoming_with_context)
            else:
                response = await self._message_handler(incoming)

            if response:
                outgoing = OutgoingMessage(
                    channel_id=incoming.channel_id,
                    text=response,
                    thread_id=incoming.thread_id,
                )
                await self.send_message(outgoing)
        except Exception as e:
            logger.exception(f"Error processing message: {e}")
            # Try to send error message back to Campfire
            try:
                error_msg = OutgoingMessage(
                    channel_id=incoming.channel_id,
                    text=f"Sorry, I encountered an error: {e}",
                )
                await self.send_message(error_msg)
            except Exception:
                logger.exception("Failed to send error message to Campfire")

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

    def _build_read_messages_url(self, room_id: str) -> str:
        """Build URL for reading messages from a room (bot read API)."""
        base = self._config.base_url.rstrip("/")
        return f"{base}/rooms/{room_id}/{self._config.bot_key}/messages"

    async def fetch_room_context(
        self, room_id: str, before_message_id: str | None = None
    ) -> list[dict]:
        """Fetch recent messages from a room for conversation context.

        Uses the bot read API (requires PR #190 or jpshackelford/once-campfire fork).

        Args:
            room_id: The room to fetch messages from
            before_message_id: Fetch messages before this ID (for pagination)

        Returns:
            List of message dicts with keys: id, body (plain/html), created_at, creator
        """
        if not self._http_session:
            raise RuntimeError("Campfire adapter not started")

        if self._config.context_messages <= 0:
            return []

        url = self._build_read_messages_url(room_id)
        params = {}
        if before_message_id:
            params["before"] = before_message_id

        try:
            async with self._http_session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    messages = data.get("messages", [])
                    # API returns newest-first; take the last N (most recent before
                    # the target message) and reverse to chronological order
                    limited = messages[-self._config.context_messages :]
                    return list(reversed(limited))
                elif resp.status == 404:
                    logger.warning(
                        "Bot read API not available (404). "
                        "Ensure you're using a Campfire image with PR #190."
                    )
                    return []
                else:
                    body = await resp.text()
                    logger.error(
                        f"Failed to fetch room context: {resp.status} - {body}"
                    )
                    return []
        except Exception as e:
            logger.warning(f"Could not fetch room context: {e}")
            return []

    def _format_context_for_prompt(
        self, messages: list[dict], current_user: str
    ) -> str:
        """Format conversation context messages into a prompt prefix.

        Args:
            messages: List of message dicts from fetch_room_context
            current_user: Name of the user who sent the current message

        Returns:
            Formatted string with conversation context
        """
        if not messages:
            return ""

        lines = ["Here is the recent conversation context from this chat room:", ""]
        for msg in messages:
            creator = msg.get("creator", {})
            name = creator.get("name", "Unknown")
            is_bot = creator.get("is_bot", False)
            body = msg.get("body", {})
            text = body.get("plain", "")

            # Mark bot messages clearly
            if is_bot:
                name = f"{name} (bot)"

            lines.append(f"**{name}**: {text}")

        lines.append("")
        lines.append("---")
        lines.append(f"Now {current_user} says:")
        lines.append("")

        return "\n".join(lines)

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

    def _build_boost_url(self, room_id: str, message_id: str) -> str:
        """Build URL for adding a reaction (boost) to a message."""
        base = self._config.base_url.rstrip("/")
        bot_key = self._config.bot_key
        return f"{base}/rooms/{room_id}/{bot_key}/messages/{message_id}/boosts"

    async def add_reaction(self, room_id: str, message_id: str, emoji: str) -> None:
        """Add an emoji reaction (boost) to a message.

        Args:
            room_id: The room containing the message
            message_id: The message to react to
            emoji: The emoji to add (e.g., "👀")
        """
        if not self._http_session:
            raise RuntimeError("Campfire adapter not started")

        url = self._build_boost_url(room_id, message_id)

        try:
            async with self._http_session.post(
                url,
                data=emoji,
                headers={"Content-Type": "text/plain; charset=utf-8"},
            ) as resp:
                if resp.status == 201:
                    logger.debug(f"Added {emoji} reaction to message {message_id}")
                elif resp.status == 404:
                    logger.warning(
                        f"Could not add reaction - message {message_id} not found "
                        f"or bot boosts API not available"
                    )
                else:
                    body = await resp.text()
                    logger.warning(f"Failed to add reaction: {resp.status} - {body}")
        except Exception as e:
            # Don't fail message processing if reaction fails
            logger.warning(f"Could not add reaction to message {message_id}: {e}")

    def is_running(self) -> bool:
        return self._running


def create_campfire_adapter(
    base_url: str,
    bot_key: str,
    webhook_port: int = 8765,
    webhook_path: str = "/webhook",
    context_messages: int = DEFAULT_CONTEXT_MESSAGES,
) -> CampfireAdapter:
    """Create a Campfire adapter with the given configuration.

    Args:
        base_url: Base URL of the Campfire instance
        bot_key: Bot authentication key
        webhook_port: Local port for webhook server
        webhook_path: URL path for webhook endpoint
        context_messages: Number of recent messages to fetch for context (0 to disable)

    Returns:
        Configured CampfireAdapter instance
    """
    config = CampfireConfig(
        base_url=base_url,
        bot_key=bot_key,
        webhook_port=webhook_port,
        webhook_path=webhook_path,
        context_messages=context_messages,
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
