"""Slack channel adapter using Socket Mode.

This adapter connects to Slack using Socket Mode, which allows real-time
event handling without requiring a public HTTP endpoint. It supports:
- App mentions (@BotName in channels)
- Direct messages
- Threaded replies

Configuration:
    channels:
      slack:
        app_token: ${SLACK_APP_TOKEN}   # xapp-... token for Socket Mode
        bot_token: ${SLACK_BOT_TOKEN}   # xoxb-... token for API calls

See docs/SLACK_SETUP.md for detailed setup instructions.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.app.async_app import AsyncApp

from openpaws.channels.base import (
    ChannelAdapter,
    IncomingMessage,
    MessageHandler,
    OutgoingMessage,
)

if TYPE_CHECKING:
    from slack_bolt.context.say.async_say import AsyncSay

logger = logging.getLogger(__name__)


@dataclass
class SlackConfig:
    """Configuration for Slack adapter.

    Attributes:
        app_token: Slack app-level token (xapp-...) for Socket Mode connection
        bot_token: Slack bot token (xoxb-...) for API calls
    """

    app_token: str
    bot_token: str

    def __post_init__(self):
        if not self.app_token.startswith("xapp-"):
            raise ValueError(
                "SLACK_APP_TOKEN must start with 'xapp-'. "
                "Get it from api.slack.com > Basic Information > App-Level Tokens"
            )
        if not self.bot_token.startswith("xoxb-"):
            raise ValueError(
                "SLACK_BOT_TOKEN must start with 'xoxb-'. "
                "Get it from api.slack.com > OAuth & Permissions"
            )


class SlackAdapter(ChannelAdapter):
    """Slack channel adapter using Socket Mode."""

    def __init__(self, config: SlackConfig):
        self._config = config
        self._app: AsyncApp | None = None
        self._handler: AsyncSocketModeHandler | None = None
        self._message_handler: MessageHandler | None = None
        self._running = False

    @property
    def channel_type(self) -> str:
        return "slack"

    def _create_incoming_message(
        self,
        event: dict,
        is_mention: bool = False,
        is_dm: bool = False,
    ) -> IncomingMessage:
        """Convert Slack event to IncomingMessage."""
        return IncomingMessage(
            channel_type=self.channel_type,
            channel_id=event.get("channel", ""),
            user_id=event.get("user", ""),
            user_name=event.get("user", ""),  # Will be resolved later if needed
            text=event.get("text", ""),
            thread_id=event.get("thread_ts") or event.get("ts"),
            is_mention=is_mention,
            is_dm=is_dm,
            raw_event=event,
        )

    async def _handle_mention(self, event: dict, say: "AsyncSay") -> None:
        """Handle app_mention events."""
        if self._message_handler is None:
            return

        msg = self._create_incoming_message(event, is_mention=True)
        logger.info(f"Received mention from {msg.user_id} in {msg.channel_id}")

        response = await self._message_handler(msg)
        if response:
            await say(text=response, thread_ts=msg.thread_id)

    async def _handle_dm(self, event: dict, say: "AsyncSay") -> None:
        """Handle direct message events."""
        if self._message_handler is None:
            return

        # Ignore bot's own messages
        if event.get("bot_id"):
            return

        msg = self._create_incoming_message(event, is_dm=True)
        logger.info(f"Received DM from {msg.user_id}")

        response = await self._message_handler(msg)
        if response:
            await say(text=response, thread_ts=msg.thread_id)

    def _setup_event_handlers(self) -> None:
        """Register event handlers with the Slack app."""
        if self._app is None:
            return

        @self._app.event("app_mention")
        async def handle_mention(event, say):
            await self._handle_mention(event, say)

        @self._app.event("message")
        async def handle_message(event, say):
            # Only handle DMs (channel type 'im')
            if event.get("channel_type") == "im":
                await self._handle_dm(event, say)

    async def start(self, message_handler: MessageHandler) -> None:
        """Start the Slack adapter with Socket Mode."""
        if self._running:
            logger.warning("Slack adapter already running")
            return

        self._message_handler = message_handler
        self._app = AsyncApp(token=self._config.bot_token)
        self._setup_event_handlers()

        self._handler = AsyncSocketModeHandler(self._app, self._config.app_token)
        self._running = True

        logger.info("Starting Slack adapter with Socket Mode")
        await self._handler.start_async()

    async def stop(self) -> None:
        """Stop the Slack adapter."""
        if not self._running:
            return

        logger.info("Stopping Slack adapter")
        if self._handler:
            await self._handler.close_async()

        self._handler = None
        self._app = None
        self._message_handler = None
        self._running = False

    async def send_message(self, message: OutgoingMessage) -> None:
        """Send a message to a Slack channel."""
        if not self._app:
            raise RuntimeError("Slack adapter not started")

        await self._app.client.chat_postMessage(
            channel=message.channel_id,
            text=message.text,
            thread_ts=message.thread_id,
        )

    def is_running(self) -> bool:
        return self._running


def create_slack_adapter(
    app_token: str,
    bot_token: str,
) -> SlackAdapter:
    """Create a Slack adapter with the given tokens.

    Args:
        app_token: Slack app-level token (xapp-...) for Socket Mode
        bot_token: Slack bot token (xoxb-...) for API calls

    Returns:
        Configured SlackAdapter instance
    """
    config = SlackConfig(app_token=app_token, bot_token=bot_token)
    return SlackAdapter(config)


async def run_slack_adapter_standalone(
    app_token: str,
    bot_token: str,
) -> None:
    """Run Slack adapter standalone for testing.

    This function runs the Slack adapter independently, logging all
    incoming messages. Useful for verifying Slack app configuration.

    Args:
        app_token: Slack app-level token
        bot_token: Slack bot token
    """

    async def echo_handler(msg: IncomingMessage) -> str | None:
        logger.info(f"Message from {msg.user_id}: {msg.text}")
        return f"Echo: {msg.text}"

    adapter = create_slack_adapter(app_token, bot_token)

    try:
        await adapter.start(echo_handler)
        # Keep running until interrupted
        while adapter.is_running():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down")
    finally:
        await adapter.stop()
