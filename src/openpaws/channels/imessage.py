"""iMessage channel adapter using BlueBubbles bridge.

This adapter connects to iMessage via BlueBubbles, a macOS app that provides
REST API and webhook access to the Messages.app database. It supports:
- Direct messages (DMs)
- Group chats
- Tapback reactions
- Read receipts and typing indicators
- Attachments (with size limits)

Configuration:
    channels:
      imessage:
        server_url: http://192.168.1.100:1234
        password: ${BLUEBUBBLES_PASSWORD}
        webhook_port: 8080  # Local port for inbound webhooks
        allowed_senders:    # Optional allowlist
          - "+15551234567"

Requirements:
    - macOS with Messages.app signed into iMessage
    - BlueBubbles server running (https://bluebubbles.app)
    - Network access between OpenPaws and BlueBubbles server

See docs/IMESSAGE_SETUP.md for detailed setup instructions.
"""

import asyncio
import logging
from dataclasses import dataclass, field

import aiohttp
from aiohttp import web

from openpaws.channels.base import (
    ChannelAdapter,
    IncomingMessage,
    MessageHandler,
    OutgoingMessage,
)

logger = logging.getLogger(__name__)

# BlueBubbles API endpoints
API_PING = "/api/v1/ping"
API_SEND_TEXT = "/api/v1/message/text"
API_SEND_REACT = "/api/v1/message/react"
API_TYPING = "/api/v1/chat/{chat_guid}/typing"
API_READ = "/api/v1/chat/{chat_guid}/read"


@dataclass
class BlueBubblesConfig:
    """Configuration for BlueBubbles iMessage adapter.

    Attributes:
        server_url: BlueBubbles server URL (e.g., http://192.168.1.100:1234)
        password: API password from BlueBubbles server settings
        webhook_port: Local port to receive webhook events (default: 8080)
        webhook_path: Path for webhook endpoint (default: /webhook)
        allowed_senders: List of phone numbers/emails allowed to send (optional)
        send_read_receipts: Whether to send read receipts (default: True)
        send_typing_indicators: Whether to send typing indicators (default: True)
        poll_interval: Seconds between polling if webhooks unavailable (default: 5)
    """

    server_url: str
    password: str
    webhook_port: int = 8080
    webhook_path: str = "/webhook"
    allowed_senders: list[str] | None = None
    send_read_receipts: bool = True
    send_typing_indicators: bool = True
    poll_interval: float = 5.0

    def _validate_server_url(self) -> None:
        """Validate server_url format."""
        if not self.server_url:
            raise ValueError("server_url is required")
        if not self.server_url.startswith(("http://", "https://")):
            raise ValueError(
                f"server_url must start with http:// or https://: {self.server_url}"
            )

    def _validate_password(self) -> None:
        """Validate password is provided."""
        if not self.password:
            raise ValueError("password is required")

    def _validate_webhook_port(self) -> None:
        """Validate webhook_port range."""
        if not 1 <= self.webhook_port <= 65535:
            raise ValueError(
                f"webhook_port must be between 1 and 65535: {self.webhook_port}"
            )

    def __post_init__(self):
        self._validate_server_url()
        self._validate_password()
        self._validate_webhook_port()
        # Normalize server_url (remove trailing slash)
        self.server_url = self.server_url.rstrip("/")


@dataclass
class WebhookEvent:
    """Parsed webhook event from BlueBubbles.

    Attributes:
        event_type: Event type (new-message, message-updated, typing-indicator, etc.)
        data: Event payload data
    """

    event_type: str
    data: dict = field(default_factory=dict)


class BlueBubblesClient:
    """HTTP client for BlueBubbles REST API."""

    def __init__(self, config: BlueBubblesConfig):
        self._config = config
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure HTTP session exists."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _build_url(self, endpoint: str) -> str:
        """Build full URL with authentication."""
        return f"{self._config.server_url}{endpoint}?password={self._config.password}"

    async def ping(self) -> bool:
        """Test connection to BlueBubbles server."""
        session = await self._ensure_session()
        try:
            async with session.get(self._build_url(API_PING)) as resp:
                return resp.status == 200
        except aiohttp.ClientError as e:
            logger.error(f"BlueBubbles ping failed: {e}")
            return False

    async def send_message(
        self, chat_guid: str, text: str, temp_guid: str | None = None
    ) -> dict:
        """Send a text message.

        Args:
            chat_guid: Chat GUID (e.g., iMessage;-;+15551234567)
            text: Message text
            temp_guid: Optional temporary GUID for deduplication

        Returns:
            API response data
        """
        session = await self._ensure_session()
        payload = {
            "chatGuid": chat_guid,
            "message": text,
        }
        if temp_guid:
            payload["tempGuid"] = temp_guid

        async with session.post(self._build_url(API_SEND_TEXT), json=payload) as resp:
            return await resp.json()

    async def send_typing(self, chat_guid: str) -> None:
        """Send typing indicator."""
        if not self._config.send_typing_indicators:
            return
        session = await self._ensure_session()
        url = self._build_url(API_TYPING.format(chat_guid=chat_guid))
        try:
            async with session.post(url) as resp:
                if resp.status != 200:
                    logger.debug(f"Typing indicator failed: {resp.status}")
        except aiohttp.ClientError as e:
            logger.debug(f"Typing indicator error: {e}")

    async def send_read_receipt(self, chat_guid: str) -> None:
        """Send read receipt for a chat."""
        if not self._config.send_read_receipts:
            return
        session = await self._ensure_session()
        url = self._build_url(API_READ.format(chat_guid=chat_guid))
        try:
            async with session.post(url) as resp:
                if resp.status != 200:
                    logger.debug(f"Read receipt failed: {resp.status}")
        except aiohttp.ClientError as e:
            logger.debug(f"Read receipt error: {e}")


class IMessageAdapter(ChannelAdapter):
    """iMessage channel adapter using BlueBubbles bridge."""

    def __init__(self, config: BlueBubblesConfig):
        self._config = config
        self._client = BlueBubblesClient(config)
        self._message_handler: MessageHandler | None = None
        self._running = False
        self._webhook_app: web.Application | None = None
        self._webhook_runner: web.AppRunner | None = None
        self._webhook_site: web.TCPSite | None = None

    @property
    def channel_type(self) -> str:
        return "imessage"

    def _is_sender_allowed(self, sender: str) -> bool:
        """Check if sender is in the allowlist."""
        if self._config.allowed_senders is None:
            return True  # No allowlist = allow all
        return sender in self._config.allowed_senders

    def _extract_sender_address(self, data: dict) -> str:
        """Extract sender address from webhook data."""
        handle = data.get("handle", {})
        if isinstance(handle, dict):
            return handle.get("address", "")
        return str(handle) if handle else ""

    def _extract_chat_guid(self, data: dict) -> str:
        """Extract chat GUID from webhook data."""
        chats = data.get("chats", [])
        if chats and isinstance(chats[0], dict):
            return chats[0].get("guid", "")
        return ""

    def _extract_chat_display_name(self, data: dict) -> str:
        """Extract chat display name for group chats."""
        chats = data.get("chats", [])
        if chats and isinstance(chats[0], dict):
            return chats[0].get("displayName", "")
        return ""

    def _is_group_chat(self, chat_guid: str) -> bool:
        """Check if chat GUID indicates a group chat."""
        # Group chats have format: iMessage;+;chat123456789
        # DMs have format: iMessage;-;+15551234567
        return ";+;" in chat_guid

    def _create_incoming_message(self, data: dict) -> IncomingMessage:
        """Convert BlueBubbles webhook data to IncomingMessage."""
        sender = self._extract_sender_address(data)
        chat_guid = self._extract_chat_guid(data)
        is_group = self._is_group_chat(chat_guid)
        display_name = self._extract_chat_display_name(data) if is_group else ""

        return IncomingMessage(
            channel_type=self.channel_type,
            channel_id=chat_guid,
            user_id=sender,
            user_name=display_name or sender,
            text=data.get("text", ""),
            thread_id=data.get("guid"),  # Message GUID for threading
            is_mention=False,  # BlueBubbles doesn't easily expose mentions
            is_dm=not is_group,
            raw_event=data,
        )

    async def _handle_webhook_request(self, request: web.Request) -> web.Response:
        """Handle incoming webhook from BlueBubbles."""
        # Verify password
        password = request.query.get("password") or request.query.get("guid")
        if password != self._config.password:
            logger.warning("Webhook request with invalid password")
            return web.Response(status=401, text="Unauthorized")

        try:
            payload = await request.json()
        except Exception as e:
            logger.error(f"Failed to parse webhook payload: {e}")
            return web.Response(status=400, text="Invalid JSON")

        event = WebhookEvent(
            event_type=payload.get("type", ""),
            data=payload.get("data", {}),
        )

        await self._process_webhook_event(event)
        return web.Response(status=200, text="OK")

    async def _process_webhook_event(self, event: WebhookEvent) -> None:
        """Process a webhook event."""
        if event.event_type == "new-message":
            await self._handle_new_message(event.data)
        elif event.event_type == "typing-indicator":
            logger.debug(f"Typing indicator: {event.data}")
        elif event.event_type == "message-updated":
            logger.debug(f"Message updated: {event.data}")
        else:
            logger.debug(f"Unhandled event type: {event.event_type}")

    async def _handle_new_message(self, data: dict) -> None:
        """Handle a new incoming message."""
        # Ignore our own messages
        if data.get("isFromMe", False):
            return

        sender = self._extract_sender_address(data)
        if not self._is_sender_allowed(sender):
            logger.info(f"Message from non-allowed sender: {sender}")
            return

        if self._message_handler is None:
            return

        incoming = self._create_incoming_message(data)
        logger.info(f"Received iMessage from {incoming.user_id}")

        # Send read receipt
        chat_guid = self._extract_chat_guid(data)
        await self._client.send_read_receipt(chat_guid)

        # Send typing indicator before processing
        await self._client.send_typing(chat_guid)

        # Process message and send response
        response = await self._message_handler(incoming)
        if response:
            outgoing = OutgoingMessage(
                channel_id=chat_guid,
                text=response,
                thread_id=incoming.thread_id,
            )
            await self.send_message(outgoing)

    async def _setup_webhook_server(self) -> None:
        """Set up the webhook HTTP server."""
        self._webhook_app = web.Application()
        self._webhook_app.router.add_post(
            self._config.webhook_path, self._handle_webhook_request
        )

        self._webhook_runner = web.AppRunner(self._webhook_app)
        await self._webhook_runner.setup()

        self._webhook_site = web.TCPSite(
            self._webhook_runner,
            "0.0.0.0",
            self._config.webhook_port,
        )
        await self._webhook_site.start()
        port = self._config.webhook_port
        logger.info(f"iMessage webhook server started on port {port}")

    async def _shutdown_webhook_server(self) -> None:
        """Shut down the webhook HTTP server."""
        if self._webhook_site:
            await self._webhook_site.stop()
            self._webhook_site = None

        if self._webhook_runner:
            await self._webhook_runner.cleanup()
            self._webhook_runner = None

        self._webhook_app = None
        logger.info("iMessage webhook server stopped")

    async def start(self, message_handler: MessageHandler) -> None:
        """Start the iMessage adapter."""
        if self._running:
            logger.warning("iMessage adapter already running")
            return

        self._message_handler = message_handler

        # Test connection to BlueBubbles
        server_url = self._config.server_url
        logger.info(f"Connecting to BlueBubbles at {server_url}")
        if not await self._client.ping():
            raise RuntimeError(f"Cannot connect to BlueBubbles at {server_url}")

        logger.info("BlueBubbles connection verified")

        # Start webhook server
        await self._setup_webhook_server()

        self._running = True
        logger.info("iMessage adapter started")

    async def stop(self) -> None:
        """Stop the iMessage adapter."""
        if not self._running:
            return

        logger.info("Stopping iMessage adapter")
        self._running = False

        await self._shutdown_webhook_server()
        await self._client.close()

        self._message_handler = None
        logger.info("iMessage adapter stopped")

    async def send_message(self, message: OutgoingMessage) -> None:
        """Send a message via iMessage."""
        if not self._running:
            raise RuntimeError("iMessage adapter not started")

        result = await self._client.send_message(
            chat_guid=message.channel_id,
            text=message.text,
        )

        status = result.get("status", -1)
        if status != 200:
            error_msg = result.get("message", "Unknown error")
            logger.error(f"Failed to send iMessage: {error_msg}")
            raise RuntimeError(f"Failed to send iMessage: {error_msg}")

        logger.info(f"Sent iMessage to {message.channel_id}")

    def is_running(self) -> bool:
        return self._running


def create_imessage_adapter(
    server_url: str,
    password: str,
    webhook_port: int = 8080,
    webhook_path: str = "/webhook",
    allowed_senders: list[str] | None = None,
    send_read_receipts: bool = True,
    send_typing_indicators: bool = True,
) -> IMessageAdapter:
    """Create an iMessage adapter with BlueBubbles.

    Args:
        server_url: BlueBubbles server URL (e.g., http://192.168.1.100:1234)
        password: API password from BlueBubbles settings
        webhook_port: Local port for webhook server (default: 8080)
        webhook_path: Path for webhook endpoint (default: /webhook)
        allowed_senders: List of allowed phone numbers/emails (None = allow all)
        send_read_receipts: Send read receipts (default: True)
        send_typing_indicators: Send typing indicators (default: True)

    Returns:
        Configured IMessageAdapter instance
    """
    config = BlueBubblesConfig(
        server_url=server_url,
        password=password,
        webhook_port=webhook_port,
        webhook_path=webhook_path,
        allowed_senders=allowed_senders,
        send_read_receipts=send_read_receipts,
        send_typing_indicators=send_typing_indicators,
    )
    return IMessageAdapter(config)


async def run_imessage_adapter_standalone(
    server_url: str,
    password: str,
    webhook_port: int = 8080,
) -> None:
    """Run iMessage adapter standalone for testing.

    This function runs the iMessage adapter independently, logging all
    incoming messages. Useful for verifying BlueBubbles configuration.

    Args:
        server_url: BlueBubbles server URL
        password: API password
        webhook_port: Local webhook port
    """

    async def echo_handler(msg: IncomingMessage) -> str | None:
        logger.info(f"iMessage from {msg.user_id}: {msg.text}")
        return f"Echo: {msg.text}"

    adapter = create_imessage_adapter(
        server_url=server_url,
        password=password,
        webhook_port=webhook_port,
    )

    try:
        await adapter.start(echo_handler)
        # Keep running until interrupted
        while adapter.is_running():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down")
    finally:
        await adapter.stop()
