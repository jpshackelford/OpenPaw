"""Tests for iMessage channel adapter using BlueBubbles."""

from unittest.mock import patch

import pytest

from openpaws.channels.base import IncomingMessage, OutgoingMessage
from openpaws.channels.imessage import (
    BlueBubblesClient,
    BlueBubblesConfig,
    IMessageAdapter,
    WebhookEvent,
    create_imessage_adapter,
)


class TestBlueBubblesConfig:
    """Tests for BlueBubblesConfig validation."""

    def test_valid_config(self):
        """Test valid configuration is accepted."""
        config = BlueBubblesConfig(
            server_url="http://192.168.1.100:1234",
            password="test-password",
        )
        assert config.server_url == "http://192.168.1.100:1234"
        assert config.password == "test-password"
        assert config.webhook_port == 8080
        assert config.webhook_path == "/webhook"
        assert config.allowed_senders is None
        assert config.send_read_receipts is True
        assert config.send_typing_indicators is True

    def test_config_with_https(self):
        """Test HTTPS server URL is accepted."""
        config = BlueBubblesConfig(
            server_url="https://bluebubbles.example.com",
            password="secure-password",
        )
        assert config.server_url == "https://bluebubbles.example.com"

    def test_config_normalizes_trailing_slash(self):
        """Test trailing slash is removed from server_url."""
        config = BlueBubblesConfig(
            server_url="http://192.168.1.100:1234/",
            password="test-password",
        )
        assert config.server_url == "http://192.168.1.100:1234"

    def test_invalid_server_url_empty(self):
        """Test empty server_url is rejected."""
        with pytest.raises(ValueError, match="server_url is required"):
            BlueBubblesConfig(server_url="", password="test")

    def test_invalid_server_url_no_scheme(self):
        """Test server_url without http(s) is rejected."""
        with pytest.raises(ValueError, match="server_url must start with http"):
            BlueBubblesConfig(server_url="192.168.1.100:1234", password="test")

    def test_invalid_password_empty(self):
        """Test empty password is rejected."""
        with pytest.raises(ValueError, match="password is required"):
            BlueBubblesConfig(server_url="http://localhost:1234", password="")

    def test_invalid_webhook_port_zero(self):
        """Test webhook_port of 0 is rejected."""
        with pytest.raises(ValueError, match="webhook_port must be between"):
            BlueBubblesConfig(
                server_url="http://localhost:1234",
                password="test",
                webhook_port=0,
            )

    def test_invalid_webhook_port_too_high(self):
        """Test webhook_port > 65535 is rejected."""
        with pytest.raises(ValueError, match="webhook_port must be between"):
            BlueBubblesConfig(
                server_url="http://localhost:1234",
                password="test",
                webhook_port=70000,
            )

    def test_config_with_allowed_senders(self):
        """Test configuration with allowed_senders list."""
        config = BlueBubblesConfig(
            server_url="http://localhost:1234",
            password="test",
            allowed_senders=["+15551234567", "user@example.com"],
        )
        assert config.allowed_senders == ["+15551234567", "user@example.com"]

    def test_config_with_custom_webhook_settings(self):
        """Test configuration with custom webhook settings."""
        config = BlueBubblesConfig(
            server_url="http://localhost:1234",
            password="test",
            webhook_port=9000,
            webhook_path="/custom-webhook",
        )
        assert config.webhook_port == 9000
        assert config.webhook_path == "/custom-webhook"

    def test_config_disable_receipts(self):
        """Test configuration with disabled read receipts and typing."""
        config = BlueBubblesConfig(
            server_url="http://localhost:1234",
            password="test",
            send_read_receipts=False,
            send_typing_indicators=False,
        )
        assert config.send_read_receipts is False
        assert config.send_typing_indicators is False


class TestIMessageAdapter:
    """Tests for IMessageAdapter."""

    @pytest.fixture
    def valid_config(self):
        """Create a valid BlueBubblesConfig for testing."""
        return BlueBubblesConfig(
            server_url="http://192.168.1.100:1234",
            password="test-password",
            webhook_port=8080,
        )

    def test_channel_type(self, valid_config):
        """Test channel type is 'imessage'."""
        adapter = IMessageAdapter(valid_config)
        assert adapter.channel_type == "imessage"

    def test_is_running_initially_false(self, valid_config):
        """Test adapter is not running when created."""
        adapter = IMessageAdapter(valid_config)
        assert adapter.is_running() is False

    def test_is_sender_allowed_no_allowlist(self, valid_config):
        """Test all senders allowed when no allowlist set."""
        adapter = IMessageAdapter(valid_config)
        assert adapter._is_sender_allowed("+15551234567") is True
        assert adapter._is_sender_allowed("anyone@example.com") is True

    def test_is_sender_allowed_with_allowlist(self):
        """Test only allowed senders pass when allowlist set."""
        config = BlueBubblesConfig(
            server_url="http://localhost:1234",
            password="test",
            allowed_senders=["+15551234567", "allowed@example.com"],
        )
        adapter = IMessageAdapter(config)

        assert adapter._is_sender_allowed("+15551234567") is True
        assert adapter._is_sender_allowed("allowed@example.com") is True
        assert adapter._is_sender_allowed("+15559999999") is False
        assert adapter._is_sender_allowed("blocked@example.com") is False

    def test_extract_sender_address_dict(self, valid_config):
        """Test extracting sender from handle dict."""
        adapter = IMessageAdapter(valid_config)
        data = {"handle": {"address": "+15551234567"}}
        assert adapter._extract_sender_address(data) == "+15551234567"

    def test_extract_sender_address_string(self, valid_config):
        """Test extracting sender when handle is string."""
        adapter = IMessageAdapter(valid_config)
        data = {"handle": "+15551234567"}
        assert adapter._extract_sender_address(data) == "+15551234567"

    def test_extract_sender_address_missing(self, valid_config):
        """Test extracting sender when handle is missing."""
        adapter = IMessageAdapter(valid_config)
        data = {}
        assert adapter._extract_sender_address(data) == ""

    def test_extract_chat_guid(self, valid_config):
        """Test extracting chat GUID from webhook data."""
        adapter = IMessageAdapter(valid_config)
        data = {"chats": [{"guid": "iMessage;-;+15551234567"}]}
        assert adapter._extract_chat_guid(data) == "iMessage;-;+15551234567"

    def test_extract_chat_guid_empty_chats(self, valid_config):
        """Test extracting chat GUID when chats is empty."""
        adapter = IMessageAdapter(valid_config)
        data = {"chats": []}
        assert adapter._extract_chat_guid(data) == ""

    def test_extract_chat_guid_missing(self, valid_config):
        """Test extracting chat GUID when missing."""
        adapter = IMessageAdapter(valid_config)
        data = {}
        assert adapter._extract_chat_guid(data) == ""

    def test_is_group_chat_dm(self, valid_config):
        """Test DM chat is not detected as group."""
        adapter = IMessageAdapter(valid_config)
        assert adapter._is_group_chat("iMessage;-;+15551234567") is False

    def test_is_group_chat_group(self, valid_config):
        """Test group chat is detected."""
        adapter = IMessageAdapter(valid_config)
        assert adapter._is_group_chat("iMessage;+;chat123456789") is True

    def test_create_incoming_message_dm(self, valid_config):
        """Test creating incoming message for DM."""
        adapter = IMessageAdapter(valid_config)
        data = {
            "guid": "msg-12345",
            "text": "Hello from iMessage!",
            "handle": {"address": "+15551234567"},
            "chats": [{"guid": "iMessage;-;+15551234567"}],
        }

        msg = adapter._create_incoming_message(data)

        assert isinstance(msg, IncomingMessage)
        assert msg.channel_type == "imessage"
        assert msg.channel_id == "iMessage;-;+15551234567"
        assert msg.user_id == "+15551234567"
        assert msg.text == "Hello from iMessage!"
        assert msg.thread_id == "msg-12345"
        assert msg.is_dm is True
        assert msg.is_mention is False
        assert msg.raw_event == data

    def test_create_incoming_message_group(self, valid_config):
        """Test creating incoming message for group chat."""
        adapter = IMessageAdapter(valid_config)
        data = {
            "guid": "msg-67890",
            "text": "Hello group!",
            "handle": {"address": "+15551234567"},
            "chats": [
                {"guid": "iMessage;+;chat123456789", "displayName": "Family Chat"}
            ],
        }

        msg = adapter._create_incoming_message(data)

        assert msg.channel_id == "iMessage;+;chat123456789"
        assert msg.user_name == "Family Chat"
        assert msg.is_dm is False


class TestCreateIMessageAdapter:
    """Tests for create_imessage_adapter factory function."""

    def test_creates_adapter_with_valid_config(self):
        """Test factory creates adapter with valid config."""
        adapter = create_imessage_adapter(
            server_url="http://192.168.1.100:1234",
            password="test-password",
        )

        assert isinstance(adapter, IMessageAdapter)
        assert adapter.channel_type == "imessage"
        assert adapter.is_running() is False

    def test_creates_adapter_with_all_options(self):
        """Test factory creates adapter with all options."""
        adapter = create_imessage_adapter(
            server_url="http://192.168.1.100:1234",
            password="test-password",
            webhook_port=9000,
            webhook_path="/custom",
            allowed_senders=["+15551234567"],
            send_read_receipts=False,
            send_typing_indicators=False,
        )

        assert isinstance(adapter, IMessageAdapter)
        assert adapter._config.webhook_port == 9000
        assert adapter._config.webhook_path == "/custom"
        assert adapter._config.allowed_senders == ["+15551234567"]
        assert adapter._config.send_read_receipts is False
        assert adapter._config.send_typing_indicators is False

    def test_raises_on_invalid_server_url(self):
        """Test factory raises on invalid server URL."""
        with pytest.raises(ValueError, match="server_url"):
            create_imessage_adapter(server_url="", password="test")

    def test_raises_on_invalid_password(self):
        """Test factory raises on invalid password."""
        with pytest.raises(ValueError, match="password"):
            create_imessage_adapter(server_url="http://localhost:1234", password="")


class TestWebhookEvent:
    """Tests for WebhookEvent dataclass."""

    def test_create_event(self):
        """Test creating a webhook event."""
        event = WebhookEvent(
            event_type="new-message",
            data={"text": "Hello", "guid": "msg-123"},
        )
        assert event.event_type == "new-message"
        assert event.data == {"text": "Hello", "guid": "msg-123"}

    def test_create_event_defaults(self):
        """Test webhook event with default data."""
        event = WebhookEvent(event_type="typing-indicator")
        assert event.event_type == "typing-indicator"
        assert event.data == {}


class TestBlueBubblesClient:
    """Tests for BlueBubblesClient."""

    @pytest.fixture
    def config(self):
        """Create config for client testing."""
        return BlueBubblesConfig(
            server_url="http://192.168.1.100:1234",
            password="test-password",
        )

    def test_build_url(self, config):
        """Test URL building with authentication."""
        client = BlueBubblesClient(config)
        url = client._build_url("/api/v1/ping")
        assert url == "http://192.168.1.100:1234/api/v1/ping?password=test-password"

    def test_build_url_with_trailing_slash_removed(self):
        """Test URL building removes trailing slash from server_url."""
        config = BlueBubblesConfig(
            server_url="http://192.168.1.100:1234/",
            password="test-password",
        )
        client = BlueBubblesClient(config)
        url = client._build_url("/api/v1/ping")
        assert url == "http://192.168.1.100:1234/api/v1/ping?password=test-password"

    @pytest.mark.asyncio
    async def test_send_typing_disabled(self, config):
        """Test typing indicator returns early when disabled."""
        config.send_typing_indicators = False
        client = BlueBubblesClient(config)

        # Should return without error when typing is disabled
        await client.send_typing("iMessage;-;+15551234567")
        # No session should be created since we return early
        assert client._session is None

        await client.close()

    @pytest.mark.asyncio
    async def test_send_read_receipt_disabled(self, config):
        """Test read receipt returns early when disabled."""
        config.send_read_receipts = False
        client = BlueBubblesClient(config)

        # Should return without error when receipts are disabled
        await client.send_read_receipt("iMessage;-;+15551234567")
        # No session should be created since we return early
        assert client._session is None

        await client.close()

    @pytest.mark.asyncio
    async def test_close_without_session(self, config):
        """Test close works when session was never created."""
        client = BlueBubblesClient(config)
        await client.close()  # Should not raise
        assert client._session is None


class TestIMessageAdapterLifecycle:
    """Tests for IMessageAdapter start/stop lifecycle."""

    @pytest.fixture
    def config(self):
        """Create valid config for lifecycle testing."""
        return BlueBubblesConfig(
            server_url="http://192.168.1.100:1234",
            password="test-password",
            webhook_port=18080,  # Use high port to avoid conflicts
        )

    @pytest.mark.asyncio
    async def test_start_verifies_connection(self, config):
        """Test that start verifies BlueBubbles connection."""
        adapter = IMessageAdapter(config)

        async def dummy_handler(msg):
            return None

        with (
            patch.object(adapter._client, "ping", return_value=True) as mock_ping,
            patch.object(adapter, "_setup_webhook_server") as mock_webhook,
        ):
            await adapter.start(dummy_handler)

            mock_ping.assert_called_once()
            mock_webhook.assert_called_once()
            assert adapter.is_running() is True
            assert adapter._message_handler is dummy_handler

        # Cleanup
        adapter._running = False

    @pytest.mark.asyncio
    async def test_start_fails_on_connection_error(self, config):
        """Test that start fails when BlueBubbles is unreachable."""
        adapter = IMessageAdapter(config)

        async def dummy_handler(msg):
            return None

        with patch.object(adapter._client, "ping", return_value=False):
            with pytest.raises(RuntimeError, match="Cannot connect to BlueBubbles"):
                await adapter.start(dummy_handler)

        assert adapter.is_running() is False

    @pytest.mark.asyncio
    async def test_start_when_already_running(self, config):
        """Test that start does nothing when already running."""
        adapter = IMessageAdapter(config)
        adapter._running = True

        async def dummy_handler(msg):
            return None

        # Should not raise and should not modify state
        with patch.object(adapter._client, "ping") as mock_ping:
            await adapter.start(dummy_handler)
            mock_ping.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self, config):
        """Test that stop cleans up all resources."""
        adapter = IMessageAdapter(config)
        adapter._running = True
        adapter._message_handler = lambda x: None

        with (
            patch.object(adapter, "_shutdown_webhook_server") as mock_shutdown,
            patch.object(adapter._client, "close") as mock_close,
        ):
            await adapter.stop()

            mock_shutdown.assert_called_once()
            mock_close.assert_called_once()
            assert adapter.is_running() is False
            assert adapter._message_handler is None

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, config):
        """Test that stop does nothing when not running."""
        adapter = IMessageAdapter(config)
        adapter._running = False

        with patch.object(adapter, "_shutdown_webhook_server") as mock_shutdown:
            await adapter.stop()
            mock_shutdown.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_success(self, config):
        """Test sending a message successfully."""
        adapter = IMessageAdapter(config)
        adapter._running = True

        with patch.object(
            adapter._client, "send_message", return_value={"status": 200}
        ) as mock_send:
            msg = OutgoingMessage(
                channel_id="iMessage;-;+15551234567",
                text="Hello!",
            )
            await adapter.send_message(msg)

            mock_send.assert_called_once_with(
                chat_guid="iMessage;-;+15551234567",
                text="Hello!",
            )

    @pytest.mark.asyncio
    async def test_send_message_failure(self, config):
        """Test handling send message failure."""
        adapter = IMessageAdapter(config)
        adapter._running = True

        with patch.object(
            adapter._client,
            "send_message",
            return_value={"status": 500, "message": "Server error"},
        ):
            msg = OutgoingMessage(
                channel_id="iMessage;-;+15551234567",
                text="Hello!",
            )
            with pytest.raises(RuntimeError, match="Failed to send iMessage"):
                await adapter.send_message(msg)

    @pytest.mark.asyncio
    async def test_send_message_not_started(self, config):
        """Test sending message when adapter not started raises error."""
        adapter = IMessageAdapter(config)
        adapter._running = False

        msg = OutgoingMessage(channel_id="iMessage;-;+15551234567", text="Hello")

        with pytest.raises(RuntimeError, match="iMessage adapter not started"):
            await adapter.send_message(msg)


class TestIMessageAdapterWebhookHandler:
    """Tests for IMessageAdapter webhook handling."""

    @pytest.fixture
    def config(self):
        """Create config for webhook testing."""
        return BlueBubblesConfig(
            server_url="http://192.168.1.100:1234",
            password="test-password",
        )

    @pytest.fixture
    def adapter(self, config):
        """Create adapter for testing."""
        return IMessageAdapter(config)

    @pytest.mark.asyncio
    async def test_process_new_message_event(self, adapter):
        """Test processing new message webhook event."""
        handler_called = False
        received_msg = None

        async def test_handler(msg):
            nonlocal handler_called, received_msg
            handler_called = True
            received_msg = msg
            return "Response"

        adapter._message_handler = test_handler

        with (
            patch.object(adapter._client, "send_read_receipt") as mock_read,
            patch.object(adapter._client, "send_typing") as mock_typing,
            patch.object(adapter, "send_message") as mock_send,
        ):
            event = WebhookEvent(
                event_type="new-message",
                data={
                    "guid": "msg-123",
                    "text": "Test message",
                    "isFromMe": False,
                    "handle": {"address": "+15551234567"},
                    "chats": [{"guid": "iMessage;-;+15551234567"}],
                },
            )
            await adapter._process_webhook_event(event)

            assert handler_called is True
            assert received_msg.text == "Test message"
            assert received_msg.user_id == "+15551234567"
            mock_read.assert_called_once()
            mock_typing.assert_called_once()
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignore_own_messages(self, adapter):
        """Test that own messages are ignored."""
        handler_called = False

        async def test_handler(msg):
            nonlocal handler_called
            handler_called = True
            return "Response"

        adapter._message_handler = test_handler

        event = WebhookEvent(
            event_type="new-message",
            data={
                "guid": "msg-123",
                "text": "My own message",
                "isFromMe": True,
                "handle": {"address": "+15551234567"},
                "chats": [{"guid": "iMessage;-;+15551234567"}],
            },
        )
        await adapter._handle_new_message(event.data)

        assert handler_called is False

    @pytest.mark.asyncio
    async def test_ignore_blocked_sender(self):
        """Test that messages from non-allowed senders are ignored."""
        config = BlueBubblesConfig(
            server_url="http://localhost:1234",
            password="test",
            allowed_senders=["+15551111111"],  # Different number
        )
        adapter = IMessageAdapter(config)

        handler_called = False

        async def test_handler(msg):
            nonlocal handler_called
            handler_called = True
            return "Response"

        adapter._message_handler = test_handler

        event = WebhookEvent(
            event_type="new-message",
            data={
                "guid": "msg-123",
                "text": "Hello",
                "isFromMe": False,
                "handle": {"address": "+15559999999"},  # Not in allowlist
                "chats": [{"guid": "iMessage;-;+15559999999"}],
            },
        )
        await adapter._handle_new_message(event.data)

        assert handler_called is False

    @pytest.mark.asyncio
    async def test_typing_indicator_event(self, adapter):
        """Test handling typing indicator event."""
        event = WebhookEvent(
            event_type="typing-indicator",
            data={"display": True},
        )
        # Should not raise
        await adapter._process_webhook_event(event)

    @pytest.mark.asyncio
    async def test_message_updated_event(self, adapter):
        """Test handling message updated event."""
        event = WebhookEvent(
            event_type="message-updated",
            data={"guid": "msg-123", "dateRead": 1234567890},
        )
        # Should not raise
        await adapter._process_webhook_event(event)

    @pytest.mark.asyncio
    async def test_unknown_event_type(self, adapter):
        """Test handling unknown event type."""
        event = WebhookEvent(
            event_type="unknown-event-type",
            data={},
        )
        # Should not raise
        await adapter._process_webhook_event(event)
