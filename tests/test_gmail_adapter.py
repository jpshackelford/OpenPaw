"""Tests for Gmail channel adapter."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openpaws.channels.base import IncomingMessage, OutgoingMessage
from openpaws.channels.gmail import (
    GmailAdapter,
    GmailConfig,
    create_gmail_adapter,
)


class TestGmailConfig:
    """Tests for GmailConfig validation."""

    @pytest.fixture
    def temp_credentials_file(self, tmp_path):
        """Create a temporary credentials file for testing."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        return str(creds_file)

    def test_valid_config(self, temp_credentials_file):
        """Test valid config is accepted."""
        config = GmailConfig(
            credentials_file=temp_credentials_file,
            mode="channel",
            poll_interval=60,
        )
        assert config.mode == "channel"
        assert config.poll_interval == 60

    def test_tool_mode_valid(self, temp_credentials_file):
        """Test tool mode is accepted."""
        config = GmailConfig(
            credentials_file=temp_credentials_file,
            mode="tool",
        )
        assert config.mode == "tool"

    def test_invalid_mode(self, temp_credentials_file):
        """Test invalid mode is rejected."""
        with pytest.raises(ValueError, match="mode must be 'channel' or 'tool'"):
            GmailConfig(
                credentials_file=temp_credentials_file,
                mode="invalid",
            )

    def test_invalid_poll_interval(self, temp_credentials_file):
        """Test poll interval below 10s is rejected."""
        with pytest.raises(ValueError, match="poll_interval must be >= 10s"):
            GmailConfig(
                credentials_file=temp_credentials_file,
                poll_interval=5,
            )

    def test_missing_credentials_file(self):
        """Test missing credentials file is rejected."""
        with pytest.raises(ValueError, match="Credentials file not found"):
            GmailConfig(
                credentials_file="/nonexistent/path/credentials.json",
            )

    def test_custom_token_file(self, temp_credentials_file, tmp_path):
        """Test custom token file path."""
        token_file = str(tmp_path / "token.json")
        config = GmailConfig(
            credentials_file=temp_credentials_file,
            token_file=token_file,
        )
        assert config.token_file == token_file

    def test_filter_label(self, temp_credentials_file):
        """Test filter label configuration."""
        config = GmailConfig(
            credentials_file=temp_credentials_file,
            filter_label="openpaws",
        )
        assert config.filter_label == "openpaws"


class TestGmailAdapter:
    """Tests for GmailAdapter."""

    @pytest.fixture
    def temp_credentials_file(self, tmp_path):
        """Create a temporary credentials file for testing."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        return str(creds_file)

    @pytest.fixture
    def valid_config(self, temp_credentials_file):
        """Create a valid GmailConfig for testing."""
        return GmailConfig(
            credentials_file=temp_credentials_file,
            mode="channel",
            poll_interval=60,
        )

    def test_channel_type(self, valid_config):
        """Test channel type is 'gmail'."""
        adapter = GmailAdapter(valid_config)
        assert adapter.channel_type == "gmail"

    def test_is_running_initially_false(self, valid_config):
        """Test adapter is not running when created."""
        adapter = GmailAdapter(valid_config)
        assert adapter.is_running() is False

    def test_get_token_path_default(self, valid_config, temp_credentials_file):
        """Test default token path is derived from credentials file."""
        adapter = GmailAdapter(valid_config)
        token_path = adapter._get_token_path()
        expected = Path(temp_credentials_file).parent / "gmail_token.json"
        assert token_path == expected

    def test_get_token_path_custom(self, temp_credentials_file, tmp_path):
        """Test custom token path is used."""
        token_file = str(tmp_path / "custom_token.json")
        config = GmailConfig(
            credentials_file=temp_credentials_file,
            token_file=token_file,
        )
        adapter = GmailAdapter(config)
        assert adapter._get_token_path() == Path(token_file)


class TestGmailAdapterMessageParsing:
    """Tests for Gmail message parsing."""

    @pytest.fixture
    def adapter(self, tmp_path):
        """Create an adapter for testing."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        config = GmailConfig(credentials_file=str(creds_file))
        return GmailAdapter(config)

    def test_extract_sender_name_with_brackets(self, adapter):
        """Test extracting sender name from email with angle brackets."""
        from_header = '"John Doe" <john@example.com>'
        name = adapter._extract_sender_name(from_header)
        assert name == "John Doe"

    def test_extract_sender_name_plain_email(self, adapter):
        """Test extracting sender name from plain email."""
        from_header = "john@example.com"
        name = adapter._extract_sender_name(from_header)
        assert name == "john@example.com"

    def test_extract_sender_name_without_quotes(self, adapter):
        """Test extracting sender name without quotes."""
        from_header = "John Doe <john@example.com>"
        name = adapter._extract_sender_name(from_header)
        assert name == "John Doe"

    def test_build_label_query_default(self, adapter):
        """Test default query is just unread."""
        query = adapter._build_label_query()
        assert query == "is:unread"

    def test_build_label_query_with_filter(self, tmp_path):
        """Test query with filter label."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        config = GmailConfig(
            credentials_file=str(creds_file),
            filter_label="openpaws",
        )
        adapter = GmailAdapter(config)
        query = adapter._build_label_query()
        assert query == "is:unread label:openpaws"

    def test_extract_headers(self, adapter):
        """Test extracting headers from message data."""
        msg_data = {
            "payload": {
                "headers": [
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "Subject", "value": "Test Subject"},
                    {"name": "To", "value": "recipient@example.com"},
                ]
            }
        }
        headers = adapter._extract_headers(msg_data)
        assert headers["From"] == "sender@example.com"
        assert headers["Subject"] == "Test Subject"
        assert headers["To"] == "recipient@example.com"

    def test_build_raw_event(self, adapter):
        """Test building raw event from message data."""
        msg_data = {
            "id": "msg123",
            "threadId": "thread456",
            "payload": {
                "headers": [
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "Subject", "value": "Test"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Date", "value": "Mon, 1 Jan 2024 12:00:00 +0000"},
                ]
            },
        }
        headers = adapter._extract_headers(msg_data)
        raw_event = adapter._build_raw_event(msg_data, headers)

        assert raw_event["id"] == "msg123"
        assert raw_event["threadId"] == "thread456"
        assert raw_event["subject"] == "Test"
        assert raw_event["from"] == "sender@example.com"

    def test_decode_body_data_empty(self, adapter):
        """Test decoding empty body data."""
        result = adapter._decode_body_data("")
        assert result == ""

    def test_decode_body_data_valid(self, adapter):
        """Test decoding valid base64 body data."""
        import base64

        text = "Hello, World!"
        encoded = base64.urlsafe_b64encode(text.encode()).decode()
        result = adapter._decode_body_data(encoded)
        assert result == text


class TestGmailAdapterBodyExtraction:
    """Tests for email body extraction."""

    @pytest.fixture
    def adapter(self, tmp_path):
        """Create an adapter for testing."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        config = GmailConfig(credentials_file=str(creds_file))
        return GmailAdapter(config)

    def test_extract_text_from_plain_payload(self, adapter):
        """Test extracting text from plain text payload."""
        import base64

        text = "Hello from email"
        encoded = base64.urlsafe_b64encode(text.encode()).decode()
        payload = {
            "mimeType": "text/plain",
            "body": {"data": encoded},
        }
        result = adapter._extract_text_from_payload(payload)
        assert result == text

    def test_extract_text_from_multipart(self, adapter):
        """Test extracting text from multipart message."""
        import base64

        text = "Plain text content"
        encoded = base64.urlsafe_b64encode(text.encode()).decode()
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": encoded},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": "aHRtbA=="},  # "html"
                },
            ],
        }
        result = adapter._extract_text_from_payload(payload)
        assert result == text

    def test_extract_text_from_nested_parts(self, adapter):
        """Test extracting text from deeply nested parts."""
        import base64

        text = "Nested text"
        encoded = base64.urlsafe_b64encode(text.encode()).decode()
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": encoded},
                        }
                    ],
                }
            ],
        }
        result = adapter._extract_text_from_payload(payload)
        assert result == text

    def test_extract_text_empty_payload(self, adapter):
        """Test extracting text from empty payload."""
        payload = {}
        result = adapter._extract_text_from_payload(payload)
        assert result == ""


class TestCreateGmailAdapter:
    """Tests for create_gmail_adapter factory function."""

    @pytest.fixture
    def temp_credentials_file(self, tmp_path):
        """Create a temporary credentials file."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        return str(creds_file)

    def test_creates_adapter_with_defaults(self, temp_credentials_file):
        """Test factory creates adapter with default settings."""
        adapter = create_gmail_adapter(credentials_file=temp_credentials_file)

        assert isinstance(adapter, GmailAdapter)
        assert adapter.channel_type == "gmail"
        assert adapter.is_running() is False

    def test_creates_adapter_with_custom_settings(self, temp_credentials_file):
        """Test factory creates adapter with custom settings."""
        adapter = create_gmail_adapter(
            credentials_file=temp_credentials_file,
            mode="tool",
            poll_interval=120,
            filter_label="test-label",
        )

        assert adapter._config.mode == "tool"
        assert adapter._config.poll_interval == 120
        assert adapter._config.filter_label == "test-label"

    def test_raises_on_invalid_mode(self, temp_credentials_file):
        """Test factory raises on invalid mode."""
        with pytest.raises(ValueError, match="mode must be 'channel' or 'tool'"):
            create_gmail_adapter(
                credentials_file=temp_credentials_file,
                mode="invalid",
            )


class TestGmailAdapterIncomingMessage:
    """Tests for creating IncomingMessage from Gmail data."""

    @pytest.fixture
    def adapter(self, tmp_path):
        """Create an adapter for testing."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        config = GmailConfig(credentials_file=str(creds_file))
        return GmailAdapter(config)

    def test_create_incoming_message(self, adapter):
        """Test creating incoming message from Gmail data."""
        import base64

        body_text = "Hello, this is a test email."
        encoded_body = base64.urlsafe_b64encode(body_text.encode()).decode()

        msg_data = {
            "id": "msg123",
            "threadId": "thread456",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": '"John Doe" <john@example.com>'},
                    {"name": "Subject", "value": "Test Subject"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Date", "value": "Mon, 1 Jan 2024 12:00:00 +0000"},
                ],
                "body": {"data": encoded_body},
            },
        }

        msg = adapter._create_incoming_message(msg_data)

        assert isinstance(msg, IncomingMessage)
        assert msg.channel_type == "gmail"
        assert msg.channel_id == '"John Doe" <john@example.com>'
        assert msg.user_id == '"John Doe" <john@example.com>'
        assert msg.user_name == "John Doe"
        assert msg.text == body_text
        assert msg.thread_id == "thread456"
        assert msg.is_mention is True
        assert msg.is_dm is True
        assert msg.raw_event["id"] == "msg123"
        assert msg.raw_event["subject"] == "Test Subject"


class TestGmailAdapterLifecycle:
    """Tests for Gmail adapter start/stop lifecycle."""

    @pytest.fixture
    def config(self, tmp_path):
        """Create a valid config for testing."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        return GmailConfig(credentials_file=str(creds_file))

    @pytest.mark.asyncio
    async def test_start_tool_mode_no_polling(self, config):
        """Test that tool mode doesn't start polling."""
        config.mode = "tool"
        adapter = GmailAdapter(config)

        async def dummy_handler(msg):
            return None

        with patch.object(adapter, "_build_service") as mock_build:
            mock_build.return_value = MagicMock()

            await adapter.start(dummy_handler)

            assert adapter.is_running() is True
            assert adapter._poll_task is None  # No polling in tool mode

            await adapter.stop()

    @pytest.mark.asyncio
    async def test_start_channel_mode_starts_polling(self, config):
        """Test that channel mode starts polling task."""
        adapter = GmailAdapter(config)

        async def dummy_handler(msg):
            return None

        with patch.object(adapter, "_build_service") as mock_build:
            mock_build.return_value = MagicMock()

            await adapter.start(dummy_handler)

            assert adapter.is_running() is True
            assert adapter._poll_task is not None

            await adapter.stop()

    @pytest.mark.asyncio
    async def test_start_when_already_running(self, config):
        """Test that start does nothing when already running."""
        adapter = GmailAdapter(config)
        adapter._running = True

        async def dummy_handler(msg):
            return None

        await adapter.start(dummy_handler)
        assert adapter._service is None  # Not modified

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self, config):
        """Test that stop cleans up all resources."""
        adapter = GmailAdapter(config)
        adapter._running = True
        adapter._service = MagicMock()
        adapter._message_handler = lambda x: None
        adapter._poll_task = asyncio.create_task(asyncio.sleep(100))

        await adapter.stop()

        assert adapter.is_running() is False
        assert adapter._service is None
        assert adapter._message_handler is None
        assert adapter._poll_task is None

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, config):
        """Test that stop does nothing when not running."""
        adapter = GmailAdapter(config)
        adapter._running = False

        await adapter.stop()
        assert adapter.is_running() is False


class TestGmailAdapterSendMessage:
    """Tests for sending messages via Gmail."""

    @pytest.fixture
    def adapter_with_service(self, tmp_path):
        """Create an adapter with mocked service."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        config = GmailConfig(credentials_file=str(creds_file))
        adapter = GmailAdapter(config)
        adapter._service = MagicMock()
        adapter._running = True
        return adapter

    def test_build_mime_message_simple(self, adapter_with_service):
        """Test building simple MIME message."""
        adapter = adapter_with_service
        msg = OutgoingMessage(
            channel_id="recipient@example.com",
            text="Hello, World!",
        )

        mime_msg = adapter._build_mime_message(msg, "Test Subject")

        assert mime_msg["to"] == "recipient@example.com"
        assert mime_msg["subject"] == "Test Subject"
        assert "References" not in mime_msg
        assert "In-Reply-To" not in mime_msg

    def test_build_mime_message_threaded(self, adapter_with_service):
        """Test building threaded MIME message."""
        adapter = adapter_with_service
        msg = OutgoingMessage(
            channel_id="recipient@example.com",
            text="Hello!",
            thread_id="thread123",
        )

        mime_msg = adapter._build_mime_message(msg, "Re: Test")

        assert mime_msg["References"] == "thread123"
        assert mime_msg["In-Reply-To"] == "thread123"

    @pytest.mark.asyncio
    async def test_send_message_not_started(self, tmp_path):
        """Test sending message when adapter not started raises error."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        config = GmailConfig(credentials_file=str(creds_file))
        adapter = GmailAdapter(config)
        adapter._service = None

        msg = OutgoingMessage(channel_id="test@example.com", text="Hello")

        with pytest.raises(RuntimeError, match="Gmail adapter not started"):
            await adapter.send_message(msg)


class TestGmailAdapterSendReply:
    """Tests for sending reply messages."""

    @pytest.fixture
    def adapter(self, tmp_path):
        """Create an adapter for testing."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        config = GmailConfig(credentials_file=str(creds_file))
        adapter = GmailAdapter(config)
        adapter._service = MagicMock()
        adapter._running = True
        return adapter

    @pytest.mark.asyncio
    async def test_send_reply_adds_re_prefix(self, adapter):
        """Test reply adds Re: prefix to subject."""
        incoming = IncomingMessage(
            channel_type="gmail",
            channel_id="sender@example.com",
            user_id="sender@example.com",
            user_name="Sender",
            text="Original message",
            raw_event={"subject": "Original Subject", "threadId": "thread123"},
        )

        with patch.object(adapter, "send_message", new_callable=AsyncMock) as mock_send:
            await adapter._send_reply(incoming, "Reply text")

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args[1]["subject"] == "Re: Original Subject"

    @pytest.mark.asyncio
    async def test_send_reply_keeps_existing_re_prefix(self, adapter):
        """Test reply doesn't double Re: prefix."""
        incoming = IncomingMessage(
            channel_type="gmail",
            channel_id="sender@example.com",
            user_id="sender@example.com",
            user_name="Sender",
            text="Message",
            raw_event={"subject": "Re: Already replied", "threadId": "thread123"},
        )

        with patch.object(adapter, "send_message", new_callable=AsyncMock) as mock_send:
            await adapter._send_reply(incoming, "Reply")

            call_args = mock_send.call_args
            assert call_args[1]["subject"] == "Re: Already replied"


class TestGmailAdapterPolling:
    """Tests for inbox polling functionality."""

    @pytest.fixture
    def adapter(self, tmp_path):
        """Create an adapter for testing."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        config = GmailConfig(credentials_file=str(creds_file), poll_interval=60)
        adapter = GmailAdapter(config)
        adapter._running = True
        return adapter

    @pytest.mark.asyncio
    async def test_fetch_unread_messages_no_service(self, adapter):
        """Test fetch returns empty list when service not available."""
        adapter._service = None
        result = await adapter._fetch_unread_messages()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_message_details_no_service(self, adapter):
        """Test get details returns None when service not available."""
        adapter._service = None
        result = await adapter._get_message_details("msg123")
        assert result is None

    @pytest.mark.asyncio
    async def test_process_message_already_processed(self, adapter):
        """Test already processed messages are skipped."""
        adapter._processed_ids.add("msg123")

        with patch.object(adapter, "_get_message_details") as mock_get:
            await adapter._process_message("msg123")
            mock_get.assert_not_called()


class TestGmailAdapterToolMode:
    """Tests for tool mode functionality."""

    @pytest.fixture
    def adapter(self, tmp_path):
        """Create an adapter in tool mode."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"installed": {"client_id": "test"}}')
        config = GmailConfig(credentials_file=str(creds_file), mode="tool")
        adapter = GmailAdapter(config)
        adapter._running = True
        return adapter

    @pytest.mark.asyncio
    async def test_get_email_returns_none_when_not_found(self, adapter):
        """Test get_email returns None when message not found."""
        with patch.object(adapter, "_get_message_details", return_value=None):
            result = await adapter.get_email("msg123")
            assert result is None

    @pytest.mark.asyncio
    async def test_search_emails_not_started(self, adapter):
        """Test search raises error when not started."""
        adapter._service = None

        with pytest.raises(RuntimeError, match="Gmail adapter not started"):
            await adapter.search_emails("query")
