"""Channel adapters for OpenPaws.

This package contains adapters for different chat platforms (Slack, Telegram, etc.).
Each adapter implements the ChannelAdapter interface for consistent message handling.
"""

from openpaws.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage
from openpaws.channels.slack import SlackAdapter, SlackConfig, create_slack_adapter

__all__ = [
    "ChannelAdapter",
    "IncomingMessage",
    "OutgoingMessage",
    "SlackAdapter",
    "SlackConfig",
    "create_slack_adapter",
]
