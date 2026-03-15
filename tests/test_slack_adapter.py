"""Tests for Slack channel adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

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


class TestSlackAdapterHandlers:
    """Tests for Slack adapter event handlers."""

    @pytest.fixture
    def adapter(self):
        """Create an adapter for testing handlers."""
        config = SlackConfig(
            app_token="xapp-1-A0123456789-0123456789012-abc123",
            bot_token="xoxb-0123456789012-0123456789012-abcdef",
        )
        return SlackAdapter(config)

    @pytest.mark.asyncio
    async def test_handle_mention_with_response(self, adapter):
        """Test handling app mention with response."""
        say_mock = AsyncMock()

        async def handler(msg):
            return f"Response to: {msg.text}"

        adapter._message_handler = handler

        event = {
            "channel": "C123",
            "user": "U456",
            "text": "Hello bot!",
            "ts": "1234567890.123456",
        }

        await adapter._handle_mention(event, say_mock)

        say_mock.assert_called_once()
        call_kwargs = say_mock.call_args[1]
        assert "Response to: Hello bot!" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_handle_mention_no_response(self, adapter):
        """Test handling app mention when handler returns None."""
        say_mock = AsyncMock()

        async def handler(msg):
            return None

        adapter._message_handler = handler

        event = {"channel": "C123", "user": "U456", "text": "Hello", "ts": "123"}

        await adapter._handle_mention(event, say_mock)

        say_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_mention_no_handler(self, adapter):
        """Test handling mention when no handler is set."""
        say_mock = AsyncMock()
        adapter._message_handler = None

        event = {"channel": "C123", "user": "U456", "text": "Hello", "ts": "123"}

        await adapter._handle_mention(event, say_mock)

        say_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_dm_with_response(self, adapter):
        """Test handling DM with response."""
        say_mock = AsyncMock()

        async def handler(msg):
            return f"DM response: {msg.text}"

        adapter._message_handler = handler

        event = {
            "channel": "D123",
            "user": "U456",
            "text": "Private message",
            "ts": "1234567890.123456",
        }

        await adapter._handle_dm(event, say_mock)

        say_mock.assert_called_once()
        call_kwargs = say_mock.call_args[1]
        assert "DM response: Private message" in call_kwargs["text"]

    @pytest.mark.asyncio
    async def test_handle_dm_ignores_bot_messages(self, adapter):
        """Test that DM handler ignores bot's own messages."""
        say_mock = AsyncMock()

        async def handler(msg):
            return "Should not be called"

        adapter._message_handler = handler

        event = {
            "channel": "D123",
            "user": "U456",
            "text": "Bot message",
            "ts": "123",
            "bot_id": "B123",  # This marks it as a bot message
        }

        await adapter._handle_dm(event, say_mock)

        say_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_dm_no_handler(self, adapter):
        """Test handling DM when no handler is set."""
        say_mock = AsyncMock()
        adapter._message_handler = None

        event = {"channel": "D123", "user": "U456", "text": "Hello", "ts": "123"}

        await adapter._handle_dm(event, say_mock)

        say_mock.assert_not_called()


class TestSlackAdapterLifecycle:
    """Tests for Slack adapter start/stop lifecycle."""

    @pytest.fixture
    def config(self):
        """Create a valid config for testing."""
        return SlackConfig(
            app_token="xapp-1-A0123456789-0123456789012-abc123",
            bot_token="xoxb-0123456789012-0123456789012-abcdef",
        )

    @pytest.mark.asyncio
    async def test_start_initializes_components(self, config):
        """Test that start initializes app and handler."""
        adapter = SlackAdapter(config)

        async def dummy_handler(msg):
            return None

        with (
            patch("openpaws.channels.slack.AsyncApp") as mock_app_cls,
            patch("openpaws.channels.slack.AsyncSocketModeHandler") as mock_handler_cls,
        ):
            mock_app = MagicMock()
            mock_app_cls.return_value = mock_app

            mock_handler = AsyncMock()
            mock_handler.start_async = AsyncMock()
            mock_handler_cls.return_value = mock_handler

            await adapter.start(dummy_handler)

            assert adapter.is_running() is True
            assert adapter._message_handler is dummy_handler
            mock_app_cls.assert_called_once_with(token=config.bot_token)
            mock_handler_cls.assert_called_once_with(mock_app, config.app_token)
            mock_handler.start_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_when_already_running(self, config):
        """Test that start does nothing when already running."""
        adapter = SlackAdapter(config)
        adapter._running = True

        async def dummy_handler(msg):
            return None

        # Should not raise and should not modify state
        await adapter.start(dummy_handler)
        assert adapter._app is None  # Not modified

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self, config):
        """Test that stop cleans up all resources."""
        adapter = SlackAdapter(config)
        adapter._running = True
        adapter._app = MagicMock()
        adapter._handler = AsyncMock()
        adapter._handler.close_async = AsyncMock()
        adapter._message_handler = lambda x: None

        await adapter.stop()

        assert adapter.is_running() is False
        assert adapter._app is None
        assert adapter._handler is None
        assert adapter._message_handler is None

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, config):
        """Test that stop does nothing when not running."""
        adapter = SlackAdapter(config)
        adapter._running = False

        # Should not raise
        await adapter.stop()
        assert adapter.is_running() is False

    @pytest.mark.asyncio
    async def test_send_message_success(self, config):
        """Test sending a message."""
        adapter = SlackAdapter(config)
        adapter._app = MagicMock()
        adapter._app.client.chat_postMessage = AsyncMock()

        msg = OutgoingMessage(
            channel_id="C123",
            text="Hello world!",
            thread_id="1234567890.123456",
        )

        await adapter.send_message(msg)

        adapter._app.client.chat_postMessage.assert_called_once_with(
            channel="C123",
            text="Hello world!",
            thread_ts="1234567890.123456",
        )

    @pytest.mark.asyncio
    async def test_send_message_not_started(self, config):
        """Test sending message when adapter not started raises error."""
        adapter = SlackAdapter(config)
        adapter._app = None

        msg = OutgoingMessage(channel_id="C123", text="Hello")

        with pytest.raises(RuntimeError, match="Slack adapter not started"):
            await adapter.send_message(msg)

    def test_setup_event_handlers_no_app(self, config):
        """Test setup_event_handlers does nothing without app."""
        adapter = SlackAdapter(config)
        adapter._app = None

        # Should not raise
        adapter._setup_event_handlers()


class TestSlackAdapterEventRegistration:
    """Tests for Slack event handler registration."""

    @pytest.fixture
    def config(self):
        return SlackConfig(
            app_token="xapp-1-A0123456789-0123456789012-abc123",
            bot_token="xoxb-0123456789012-0123456789012-abcdef",
        )

    def test_setup_event_handlers_registers_events(self, config):
        """Test that event handlers are registered with the app."""
        adapter = SlackAdapter(config)

        mock_app = MagicMock()
        mock_app.event = MagicMock(return_value=lambda f: f)
        adapter._app = mock_app

        adapter._setup_event_handlers()

        # Verify event decorators were called
        calls = mock_app.event.call_args_list
        event_names = [call[0][0] for call in calls]
        assert "app_mention" in event_names
        assert "message" in event_names
