"""Tests for Slack channel adapter."""

import pytest

from openpaws.channels.base import IncomingMessage, OutgoingMessage
from openpaws.channels.slack import SlackAdapter, SlackConfig, create_slack_adapter


class TestSlackConfig:
    """Tests for SlackConfig validation."""

    def test_valid_config(self):
        """Test valid token formats are accepted."""
        config = SlackConfig(
            app_token="xapp-1-A0123456789-0123456789012-abc123",
            bot_token="xoxb-0123456789012-0123456789012-abcdefghijklmnop",
        )
        assert config.app_token.startswith("xapp-")
        assert config.bot_token.startswith("xoxb-")

    def test_invalid_app_token(self):
        """Test invalid app token is rejected."""
        with pytest.raises(ValueError, match="SLACK_APP_TOKEN must start with 'xapp-'"):
            SlackConfig(
                app_token="invalid-token",
                bot_token="xoxb-valid-token-here",
            )

    def test_invalid_bot_token(self):
        """Test invalid bot token is rejected."""
        with pytest.raises(ValueError, match="SLACK_BOT_TOKEN must start with 'xoxb-'"):
            SlackConfig(
                app_token="xapp-valid-token-here",
                bot_token="invalid-token",
            )


class TestSlackAdapter:
    """Tests for SlackAdapter."""

    @pytest.fixture
    def valid_config(self):
        """Create a valid SlackConfig for testing."""
        return SlackConfig(
            app_token="xapp-1-A0123456789-0123456789012-abc123def456",
            bot_token="xoxb-0123456789012-0123456789012-abcdefghijklmnop",
        )

    def test_channel_type(self, valid_config):
        """Test channel type is 'slack'."""
        adapter = SlackAdapter(valid_config)
        assert adapter.channel_type == "slack"

    def test_is_running_initially_false(self, valid_config):
        """Test adapter is not running when created."""
        adapter = SlackAdapter(valid_config)
        assert adapter.is_running() is False

    def test_create_incoming_message(self, valid_config):
        """Test creating incoming message from Slack event."""
        adapter = SlackAdapter(valid_config)

        event = {
            "channel": "C0123456789",
            "user": "U9876543210",
            "text": "Hello @bot!",
            "ts": "1234567890.123456",
            "thread_ts": "1234567890.000000",
        }

        msg = adapter._create_incoming_message(event, is_mention=True, is_dm=False)

        assert isinstance(msg, IncomingMessage)
        assert msg.channel_type == "slack"
        assert msg.channel_id == "C0123456789"
        assert msg.user_id == "U9876543210"
        assert msg.text == "Hello @bot!"
        assert msg.thread_id == "1234567890.000000"
        assert msg.is_mention is True
        assert msg.is_dm is False
        assert msg.raw_event == event

    def test_create_incoming_message_dm(self, valid_config):
        """Test creating incoming message for DM."""
        adapter = SlackAdapter(valid_config)

        event = {
            "channel": "D0123456789",
            "user": "U9876543210",
            "text": "Private message",
            "ts": "1234567890.123456",
        }

        msg = adapter._create_incoming_message(event, is_mention=False, is_dm=True)

        assert msg.is_dm is True
        assert msg.is_mention is False
        assert msg.thread_id == "1234567890.123456"  # Uses ts when no thread_ts

    def test_create_incoming_message_missing_fields(self, valid_config):
        """Test handling missing fields gracefully."""
        adapter = SlackAdapter(valid_config)

        event = {}  # Empty event

        msg = adapter._create_incoming_message(event)

        assert msg.channel_id == ""
        assert msg.user_id == ""
        assert msg.text == ""
        assert msg.thread_id is None


class TestCreateSlackAdapter:
    """Tests for create_slack_adapter factory function."""

    def test_creates_adapter_with_valid_tokens(self):
        """Test factory creates adapter with valid tokens."""
        adapter = create_slack_adapter(
            app_token="xapp-1-A0123456789-0123456789012-abc123",
            bot_token="xoxb-0123456789012-0123456789012-abcdef",
        )

        assert isinstance(adapter, SlackAdapter)
        assert adapter.channel_type == "slack"
        assert adapter.is_running() is False

    def test_raises_on_invalid_app_token(self):
        """Test factory raises on invalid app token."""
        with pytest.raises(ValueError, match="SLACK_APP_TOKEN"):
            create_slack_adapter(
                app_token="bad-token",
                bot_token="xoxb-valid",
            )

    def test_raises_on_invalid_bot_token(self):
        """Test factory raises on invalid bot token."""
        with pytest.raises(ValueError, match="SLACK_BOT_TOKEN"):
            create_slack_adapter(
                app_token="xapp-valid",
                bot_token="bad-token",
            )


class TestOutgoingMessage:
    """Tests for OutgoingMessage dataclass."""

    def test_create_simple_message(self):
        """Test creating a simple outgoing message."""
        msg = OutgoingMessage(
            channel_id="C0123456789",
            text="Hello, world!",
        )

        assert msg.channel_id == "C0123456789"
        assert msg.text == "Hello, world!"
        assert msg.thread_id is None

    def test_create_threaded_message(self):
        """Test creating a threaded reply message."""
        msg = OutgoingMessage(
            channel_id="C0123456789",
            text="Replying in thread",
            thread_id="1234567890.123456",
        )

        assert msg.thread_id == "1234567890.123456"


class TestIncomingMessage:
    """Tests for IncomingMessage dataclass."""

    def test_create_full_message(self):
        """Test creating a fully populated incoming message."""
        msg = IncomingMessage(
            channel_type="slack",
            channel_id="C0123456789",
            user_id="U9876543210",
            user_name="testuser",
            text="Hello!",
            thread_id="1234567890.123456",
            is_mention=True,
            is_dm=False,
            raw_event={"key": "value"},
        )

        assert msg.channel_type == "slack"
        assert msg.channel_id == "C0123456789"
        assert msg.user_id == "U9876543210"
        assert msg.user_name == "testuser"
        assert msg.text == "Hello!"
        assert msg.thread_id == "1234567890.123456"
        assert msg.is_mention is True
        assert msg.is_dm is False
        assert msg.raw_event == {"key": "value"}

    def test_create_minimal_message(self):
        """Test creating a message with minimal fields."""
        msg = IncomingMessage(
            channel_type="slack",
            channel_id="C123",
            user_id="U123",
            user_name="user",
            text="Hi",
        )

        assert msg.thread_id is None
        assert msg.is_mention is False
        assert msg.is_dm is False
        assert msg.raw_event == {}
