"""Channel adapters for OpenPaws.

This package contains adapters for different chat platforms (Slack, Gmail,
iMessage, etc.). Each adapter implements the ChannelAdapter interface for
consistent message handling.
"""

from openpaws.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage
from openpaws.channels.gmail import GmailAdapter, GmailConfig, create_gmail_adapter
from openpaws.channels.imessage import (
    BlueBubblesConfig,
    IMessageAdapter,
    create_imessage_adapter,
)
from openpaws.channels.slack import SlackAdapter, SlackConfig, create_slack_adapter

__all__ = [
    "ChannelAdapter",
    "IncomingMessage",
    "OutgoingMessage",
    "GmailAdapter",
    "GmailConfig",
    "create_gmail_adapter",
    "IMessageAdapter",
    "BlueBubblesConfig",
    "create_imessage_adapter",
    "SlackAdapter",
    "SlackConfig",
    "create_slack_adapter",
]
