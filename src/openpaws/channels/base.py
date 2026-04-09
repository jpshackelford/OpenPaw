"""Base channel adapter interface.

All channel adapters (Slack, Telegram, etc.) must implement the ChannelAdapter
abstract base class for consistent message handling across platforms.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field

# Callback types for status updates during message processing
StatusCallback = Callable[[], Awaitable[None]]
SendCallback = Callable[[str], Awaitable[None]]


@dataclass
class IncomingMessage:
    """A message received from a channel.

    Attributes:
        channel_type: The type of channel (e.g., "slack", "telegram")
        channel_id: Platform-specific channel/conversation ID
        user_id: Platform-specific user ID
        user_name: Display name of the user
        text: The message text content
        thread_id: Thread/reply identifier if in a thread (optional)
        is_mention: Whether the bot was explicitly mentioned
        is_dm: Whether this is a direct message
        raw_event: The original platform-specific event data
        on_processing_start: Optional callback when handler starts processing
        send_status: Optional callback to send interim status messages
    """

    channel_type: str
    channel_id: str
    user_id: str
    user_name: str
    text: str
    thread_id: str | None = None
    is_mention: bool = False
    is_dm: bool = False
    raw_event: dict = field(default_factory=dict)
    # Optional callbacks for status updates (set by adapters that support them)
    on_processing_start: StatusCallback | None = None
    send_status: SendCallback | None = None


@dataclass
class OutgoingMessage:
    """A message to send to a channel.

    Attributes:
        channel_id: Platform-specific channel/conversation ID
        text: The message text to send
        thread_id: Thread to reply in (optional, for threaded replies)
    """

    channel_id: str
    text: str
    thread_id: str | None = None


# Type alias for message handler callback
MessageHandler = Callable[[IncomingMessage], Coroutine[None, None, str | None]]


class ChannelAdapter(ABC):
    """Abstract base class for channel adapters.

    Each platform (Slack, Telegram, etc.) implements this interface to provide
    consistent message handling regardless of the underlying chat service.
    """

    @property
    @abstractmethod
    def channel_type(self) -> str:
        """Return the channel type identifier (e.g., 'slack', 'telegram')."""
        ...

    @abstractmethod
    async def start(self, message_handler: MessageHandler) -> None:
        """Start the channel adapter and begin listening for messages.

        Args:
            message_handler: Async callback to handle incoming messages.
                            Returns response text or None.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel adapter and clean up resources."""
        ...

    @abstractmethod
    async def send_message(self, message: OutgoingMessage) -> None:
        """Send a message to a channel.

        Args:
            message: The message to send.
        """
        ...

    @abstractmethod
    def is_running(self) -> bool:
        """Check if the adapter is currently running."""
        ...
