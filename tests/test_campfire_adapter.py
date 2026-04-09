"""Tests for Campfire channel adapter."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from openpaws.channels.base import IncomingMessage, OutgoingMessage
from openpaws.channels.campfire import (
    CampfireAdapter,
    CampfireConfig,
    create_campfire_adapter,
)


class TestCampfireConfig:
    """Tests for CampfireConfig validation."""

    def test_valid_config(self):
        """Test valid config is accepted."""
        config = CampfireConfig(
            base_url="https://chat.example.com",
            bot_key="123-abc123xyz456",
            webhook_port=8765,
            webhook_path="/webhook",
        )
        assert config.base_url == "https://chat.example.com"
        assert config.bot_key == "123-abc123xyz456"
        assert config.webhook_port == 8765
        assert config.webhook_path == "/webhook"

    def test_valid_config_http(self):
        """Test HTTP base_url is accepted."""
        config = CampfireConfig(
            base_url="http://localhost:3000",
            bot_key="1-token",
        )
        assert config.base_url == "http://localhost:3000"

    def test_missing_base_url(self):
        """Test missing base_url is rejected."""
        with pytest.raises(ValueError, match="base_url is required"):
            CampfireConfig(base_url="", bot_key="123-token")

    def test_invalid_base_url_scheme(self):
        """Test base_url without http/https is rejected."""
        with pytest.raises(ValueError, match="must start with http://"):
            CampfireConfig(base_url="ftp://example.com", bot_key="123-token")

    def test_missing_bot_key(self):
        """Test missing bot_key is rejected."""
        with pytest.raises(ValueError, match="bot_key is required"):
            CampfireConfig(base_url="https://example.com", bot_key="")

    def test_invalid_bot_key_format(self):
        """Test bot_key without dash is rejected."""
        with pytest.raises(ValueError, match="must be in format"):
            CampfireConfig(base_url="https://example.com", bot_key="invalidkey")

    def test_invalid_webhook_port_low(self):
        """Test webhook_port < 1 is rejected."""
        with pytest.raises(ValueError, match="must be 1-65535"):
            CampfireConfig(
                base_url="https://example.com",
                bot_key="123-token",
                webhook_port=0,
            )

    def test_invalid_webhook_port_high(self):
        """Test webhook_port > 65535 is rejected."""
        with pytest.raises(ValueError, match="must be 1-65535"):
            CampfireConfig(
                base_url="https://example.com",
                bot_key="123-token",
                webhook_port=70000,
            )


class TestCampfireAdapter:
    """Tests for CampfireAdapter."""

    @pytest.fixture
    def valid_config(self):
        """Create a valid CampfireConfig for testing."""
        return CampfireConfig(
            base_url="https://chat.example.com",
            bot_key="123-abc123xyz456",
            webhook_port=8765,
            webhook_path="/webhook",
        )

    def test_channel_type(self, valid_config):
        """Test channel type is 'campfire'."""
        adapter = CampfireAdapter(valid_config)
        assert adapter.channel_type == "campfire"

    def test_is_running_initially_false(self, valid_config):
        """Test adapter is not running when created."""
        adapter = CampfireAdapter(valid_config)
        assert adapter.is_running() is False

    def test_extract_room_id_from_path(self, valid_config):
        """Test extracting room ID from room path."""
        adapter = CampfireAdapter(valid_config)

        assert adapter._extract_room_id("/rooms/123/botkey/messages") == "123"
        assert adapter._extract_room_id("/rooms/456/key/messages") == "456"
        assert adapter._extract_room_id("/rooms/1/abc-xyz/messages") == "1"

    def test_extract_room_id_invalid_path(self, valid_config):
        """Test extracting room ID from invalid path."""
        adapter = CampfireAdapter(valid_config)

        assert adapter._extract_room_id("/invalid/path") == ""
        assert adapter._extract_room_id("") == ""

    def test_create_incoming_message(self, valid_config):
        """Test creating incoming message from Campfire webhook payload."""
        adapter = CampfireAdapter(valid_config)

        payload = {
            "user": {"id": 42, "name": "John Doe"},
            "room": {"id": 1, "name": "General", "path": "/rooms/1/123-abc/messages"},
            "message": {
                "id": 999,
                "body": {"html": "<p>Hello bot!</p>", "plain": "Hello bot!"},
                "path": "/rooms/1@999",
            },
        }

        msg = adapter._create_incoming_message(payload)

        assert isinstance(msg, IncomingMessage)
        assert msg.channel_type == "campfire"
        assert msg.channel_id == "1"
        assert msg.user_id == "42"
        assert msg.user_name == "John Doe"
        assert msg.text == "Hello bot!"
        assert msg.thread_id == "999"
        assert msg.is_mention is True
        assert msg.is_dm is False
        assert msg.raw_event["user"] == payload["user"]
        assert msg.raw_event["room"] == payload["room"]
        assert msg.raw_event["message"] == payload["message"]

    def test_create_incoming_message_empty_payload(self, valid_config):
        """Test creating incoming message from empty payload."""
        adapter = CampfireAdapter(valid_config)

        msg = adapter._create_incoming_message({})

        assert msg.channel_id == ""
        assert msg.user_id == ""
        assert msg.user_name == ""
        assert msg.text == ""
        assert msg.thread_id == ""

    def test_create_incoming_message_missing_body(self, valid_config):
        """Test creating incoming message when body is missing."""
        adapter = CampfireAdapter(valid_config)

        payload = {
            "user": {"id": 1, "name": "Test"},
            "room": {"id": 2, "path": "/rooms/2/key/messages"},
            "message": {"id": 3},  # No body
        }

        msg = adapter._create_incoming_message(payload)

        assert msg.text == ""

    def test_create_incoming_message_has_callbacks(self, valid_config):
        """Test that incoming message has status callbacks set."""
        adapter = CampfireAdapter(valid_config)

        payload = {
            "user": {"id": 42, "name": "Test"},
            "room": {"id": 1, "path": "/rooms/1/key/messages"},
            "message": {"id": 999, "body": {"plain": "Hello"}},
        }

        msg = adapter._create_incoming_message(payload)

        # Callbacks should be set
        assert msg.on_processing_start is not None
        assert msg.send_status is not None
        assert callable(msg.on_processing_start)
        assert callable(msg.send_status)

    @pytest.mark.asyncio
    async def test_on_processing_start_adds_reaction(self, valid_config):
        """Test that on_processing_start callback adds 👀 reaction."""
        adapter = CampfireAdapter(valid_config)

        payload = {
            "user": {"id": 42, "name": "Test"},
            "room": {"id": 1, "path": "/rooms/1/key/messages"},
            "message": {"id": 999, "body": {"plain": "Hello"}},
        }

        msg = adapter._create_incoming_message(payload)

        # Mock add_reaction
        adapter.add_reaction = AsyncMock()

        await msg.on_processing_start()

        adapter.add_reaction.assert_called_once_with("1", "999", "👀")

    @pytest.mark.asyncio
    async def test_send_status_sends_message(self, valid_config):
        """Test that send_status callback sends a message."""
        adapter = CampfireAdapter(valid_config)

        payload = {
            "user": {"id": 42, "name": "Test"},
            "room": {"id": 1, "path": "/rooms/1/key/messages"},
            "message": {"id": 999, "body": {"plain": "Hello"}},
        }

        msg = adapter._create_incoming_message(payload)

        # Mock send_message
        adapter.send_message = AsyncMock()

        await msg.send_status("I'm working on it...")

        adapter.send_message.assert_called_once()
        outgoing = adapter.send_message.call_args[0][0]
        assert outgoing.channel_id == "1"
        assert outgoing.text == "I'm working on it..."
        assert outgoing.thread_id == "999"

    def test_build_message_url(self, valid_config):
        """Test building message URL for a room."""
        adapter = CampfireAdapter(valid_config)

        url = adapter._build_message_url("42")

        assert url == "https://chat.example.com/rooms/42/123-abc123xyz456/messages"

    def test_build_message_url_strips_trailing_slash(self):
        """Test message URL handles trailing slash in base_url."""
        config = CampfireConfig(
            base_url="https://chat.example.com/",
            bot_key="1-token",
        )
        adapter = CampfireAdapter(config)

        url = adapter._build_message_url("5")

        assert url == "https://chat.example.com/rooms/5/1-token/messages"


class TestCreateCampfireAdapter:
    """Tests for create_campfire_adapter factory function."""

    def test_creates_adapter_with_valid_params(self):
        """Test factory creates adapter with valid parameters."""
        adapter = create_campfire_adapter(
            base_url="https://chat.example.com",
            bot_key="123-abc",
            webhook_port=9000,
            webhook_path="/hook",
        )

        assert isinstance(adapter, CampfireAdapter)
        assert adapter.channel_type == "campfire"
        assert adapter.is_running() is False

    def test_creates_adapter_with_defaults(self):
        """Test factory uses defaults for optional parameters."""
        adapter = create_campfire_adapter(
            base_url="https://chat.example.com",
            bot_key="123-abc",
        )

        assert isinstance(adapter, CampfireAdapter)

    def test_raises_on_invalid_base_url(self):
        """Test factory raises on invalid base_url."""
        with pytest.raises(ValueError):
            create_campfire_adapter(
                base_url="invalid",
                bot_key="123-token",
            )

    def test_raises_on_invalid_bot_key(self):
        """Test factory raises on invalid bot_key."""
        with pytest.raises(ValueError):
            create_campfire_adapter(
                base_url="https://example.com",
                bot_key="nodashinkey",
            )


class TestCampfireAdapterWebhookHandler:
    """Tests for Campfire adapter webhook handling."""

    @pytest.fixture
    def adapter(self):
        """Create an adapter for testing handlers."""
        config = CampfireConfig(
            base_url="https://chat.example.com",
            bot_key="123-abc",
        )
        return CampfireAdapter(config)

    @pytest.mark.asyncio
    async def test_handle_webhook_with_response(self, adapter):
        """Test webhook handler acknowledges immediately with 204.

        The webhook now returns 204 immediately and processes the message
        in the background, sending responses via the Campfire API.
        """

        async def handler(msg):
            return f"Response to: {msg.text}"

        adapter._message_handler = handler
        # Mock fetch_room_context to avoid needing a real HTTP session
        adapter.fetch_room_context = AsyncMock(return_value=[])

        request = AsyncMock()
        request.json = AsyncMock(
            return_value={
                "user": {"id": 1, "name": "Test"},
                "room": {"id": 2, "path": "/rooms/2/key/messages"},
                "message": {"id": 3, "body": {"plain": "Hello"}},
            }
        )

        response = await adapter._handle_webhook(request)

        # Webhook always returns 204 No Content immediately
        assert response.status == 204

    @pytest.mark.asyncio
    async def test_handle_webhook_no_response(self, adapter):
        """Test webhook handler returns 204 when handler returns None."""

        async def handler(msg):
            return None

        adapter._message_handler = handler
        # Mock fetch_room_context to avoid needing a real HTTP session
        adapter.fetch_room_context = AsyncMock(return_value=[])

        request = AsyncMock()
        request.json = AsyncMock(
            return_value={
                "user": {"id": 1, "name": "Test"},
                "room": {"id": 2, "path": "/rooms/2/key/messages"},
                "message": {"id": 3, "body": {"plain": "Hello"}},
            }
        )

        response = await adapter._handle_webhook(request)

        assert response.status == 204

    @pytest.mark.asyncio
    async def test_handle_webhook_no_handler(self, adapter):
        """Test webhook returns 503 when no handler is set."""
        adapter._message_handler = None

        request = AsyncMock()

        response = await adapter._handle_webhook(request)

        assert response.status == 503

    @pytest.mark.asyncio
    async def test_handle_webhook_invalid_json(self, adapter):
        """Test webhook returns 400 on invalid JSON."""

        async def handler(msg):
            return "Response"

        adapter._message_handler = handler

        request = AsyncMock()
        request.json = AsyncMock(side_effect=json.JSONDecodeError("", "", 0))

        response = await adapter._handle_webhook(request)

        assert response.status == 400

    @pytest.mark.asyncio
    async def test_handle_webhook_handler_error(self, adapter):
        """Test webhook returns 204 even on handler error.

        The webhook acknowledges immediately with 204. Handler errors
        are processed asynchronously and logged, not returned in the response.
        """

        async def handler(msg):
            raise RuntimeError("Handler failed")

        adapter._message_handler = handler
        # Mock fetch_room_context to avoid needing a real HTTP session
        adapter.fetch_room_context = AsyncMock(return_value=[])

        request = AsyncMock()
        request.json = AsyncMock(
            return_value={
                "user": {"id": 1, "name": "Test"},
                "room": {"id": 2, "path": "/rooms/2/key/messages"},
                "message": {"id": 3, "body": {"plain": "Hello"}},
            }
        )

        response = await adapter._handle_webhook(request)

        # Webhook still returns 204 - errors are handled asynchronously
        assert response.status == 204

    @pytest.mark.asyncio
    async def test_health_check(self, adapter):
        """Test health check endpoint."""
        request = AsyncMock()

        response = await adapter._health_check(request)

        assert response.status == 200
        assert response.text == "OK"


class TestCampfireAdapterLifecycle:
    """Tests for Campfire adapter start/stop lifecycle."""

    @pytest.fixture
    def config(self):
        """Create a valid config for testing."""
        return CampfireConfig(
            base_url="https://chat.example.com",
            bot_key="123-abc",
            webhook_port=8765,
        )

    @pytest.mark.asyncio
    async def test_start_initializes_components(self, config):
        """Test that start initializes app, runner, and site."""
        adapter = CampfireAdapter(config)

        async def dummy_handler(msg):
            return None

        with patch("openpaws.channels.campfire.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session_cls.return_value = mock_session

            # We need to patch the web components to avoid actually binding to port
            with patch.object(adapter, "_setup_routes") as mock_setup:
                mock_app = MagicMock()
                mock_setup.return_value = mock_app

                runner_patch = "openpaws.channels.campfire.web.AppRunner"
                with patch(runner_patch) as mock_runner_cls:
                    mock_runner = AsyncMock()
                    mock_runner.setup = AsyncMock()
                    mock_runner_cls.return_value = mock_runner

                    site_patch = "openpaws.channels.campfire.web.TCPSite"
                    with patch(site_patch) as mock_site_cls:
                        mock_site = AsyncMock()
                        mock_site.start = AsyncMock()
                        mock_site_cls.return_value = mock_site

                        await adapter.start(dummy_handler)

                        assert adapter.is_running() is True
                        assert adapter._message_handler is dummy_handler
                        mock_session_cls.assert_called_once()
                        mock_runner.setup.assert_called_once()
                        mock_site.start.assert_called_once()

                        # Cleanup
                        adapter._running = False

    @pytest.mark.asyncio
    async def test_start_when_already_running(self, config):
        """Test that start does nothing when already running."""
        adapter = CampfireAdapter(config)
        adapter._running = True

        async def dummy_handler(msg):
            return None

        # Should not raise and should not modify state
        await adapter.start(dummy_handler)
        assert adapter._app is None  # Not modified

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self, config):
        """Test that stop cleans up all resources."""
        adapter = CampfireAdapter(config)
        adapter._running = True
        adapter._app = MagicMock()
        adapter._site = AsyncMock()
        adapter._site.stop = AsyncMock()
        adapter._runner = AsyncMock()
        adapter._runner.cleanup = AsyncMock()
        adapter._http_session = AsyncMock()
        adapter._http_session.close = AsyncMock()
        adapter._message_handler = lambda x: None

        await adapter.stop()

        assert adapter.is_running() is False
        assert adapter._app is None
        assert adapter._site is None
        assert adapter._runner is None
        assert adapter._http_session is None
        assert adapter._message_handler is None

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, config):
        """Test that stop does nothing when not running."""
        adapter = CampfireAdapter(config)
        adapter._running = False

        # Should not raise
        await adapter.stop()
        assert adapter.is_running() is False


class TestCampfireAdapterSendMessage:
    """Tests for Campfire adapter send_message."""

    @pytest.fixture
    def config(self):
        """Create a valid config for testing."""
        return CampfireConfig(
            base_url="https://chat.example.com",
            bot_key="123-abc",
        )

    @pytest.mark.asyncio
    async def test_send_message_success(self, config):
        """Test sending a message successfully."""
        adapter = CampfireAdapter(config)

        mock_response = AsyncMock()
        mock_response.status = 201
        mock_response.headers = {"Location": "/rooms/1@999"}
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        adapter._http_session = mock_session

        msg = OutgoingMessage(
            channel_id="1",
            text="Hello from bot!",
        )

        await adapter.send_message(msg)

        mock_session.post.assert_called_once_with(
            "https://chat.example.com/rooms/1/123-abc/messages",
            data="Hello from bot!",
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )

    @pytest.mark.asyncio
    async def test_send_message_failure(self, config):
        """Test sending a message with API error."""
        adapter = CampfireAdapter(config)

        mock_response = AsyncMock()
        mock_response.status = 403
        mock_response.text = AsyncMock(return_value="Forbidden")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        adapter._http_session = mock_session

        msg = OutgoingMessage(channel_id="1", text="Hello")

        with pytest.raises(RuntimeError, match="Campfire API error: 403"):
            await adapter.send_message(msg)

    @pytest.mark.asyncio
    async def test_send_message_not_started(self, config):
        """Test sending message when adapter not started raises error."""
        adapter = CampfireAdapter(config)
        adapter._http_session = None

        msg = OutgoingMessage(channel_id="1", text="Hello")

        with pytest.raises(RuntimeError, match="Campfire adapter not started"):
            await adapter.send_message(msg)


class TestCampfireAdapterAddReaction:
    """Tests for Campfire adapter add_reaction (boosts)."""

    @pytest.fixture
    def config(self):
        """Create a valid config for testing."""
        return CampfireConfig(
            base_url="https://chat.example.com",
            bot_key="123-abc",
        )

    def test_build_boost_url(self, config):
        """Test building the boost URL."""
        adapter = CampfireAdapter(config)
        url = adapter._build_boost_url("42", "999")
        assert url == "https://chat.example.com/rooms/42/123-abc/messages/999/boosts"

    def test_build_boost_url_strips_trailing_slash(self, config):
        """Test that trailing slash is stripped from base URL."""
        config.base_url = "https://chat.example.com/"
        adapter = CampfireAdapter(config)
        url = adapter._build_boost_url("42", "999")
        assert url == "https://chat.example.com/rooms/42/123-abc/messages/999/boosts"

    @pytest.mark.asyncio
    async def test_add_reaction_success(self, config):
        """Test adding a reaction successfully."""
        adapter = CampfireAdapter(config)

        mock_response = AsyncMock()
        mock_response.status = 201
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        adapter._http_session = mock_session

        await adapter.add_reaction("42", "999", "👀")

        mock_session.post.assert_called_once_with(
            "https://chat.example.com/rooms/42/123-abc/messages/999/boosts",
            data="👀",
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )

    @pytest.mark.asyncio
    async def test_add_reaction_404_does_not_raise(self, config):
        """Test that 404 response logs warning but doesn't raise."""
        adapter = CampfireAdapter(config)

        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        adapter._http_session = mock_session

        # Should not raise, just log warning
        await adapter.add_reaction("42", "999", "👀")

    @pytest.mark.asyncio
    async def test_add_reaction_error_does_not_raise(self, config):
        """Test that API errors log warning but don't raise."""
        adapter = CampfireAdapter(config)

        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)
        adapter._http_session = mock_session

        # Should not raise, just log warning
        await adapter.add_reaction("42", "999", "👀")

    @pytest.mark.asyncio
    async def test_add_reaction_exception_does_not_raise(self, config):
        """Test that network exceptions log warning but don't raise."""
        adapter = CampfireAdapter(config)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=Exception("Network error"))
        adapter._http_session = mock_session

        # Should not raise, just log warning
        await adapter.add_reaction("42", "999", "👀")

    @pytest.mark.asyncio
    async def test_add_reaction_not_started(self, config):
        """Test adding reaction when adapter not started raises error."""
        adapter = CampfireAdapter(config)
        adapter._http_session = None

        with pytest.raises(RuntimeError, match="Campfire adapter not started"):
            await adapter.add_reaction("42", "999", "👀")


class TestCampfireAdapterRoutes:
    """Tests for Campfire adapter route setup."""

    @pytest.fixture
    def config(self):
        """Create a valid config for testing."""
        return CampfireConfig(
            base_url="https://chat.example.com",
            bot_key="123-abc",
            webhook_path="/my-webhook",
        )

    def test_setup_routes_creates_app(self, config):
        """Test that setup_routes creates a web application."""
        adapter = CampfireAdapter(config)

        app = adapter._setup_routes()

        assert isinstance(app, web.Application)

    def test_setup_routes_registers_webhook(self, config):
        """Test that setup_routes registers webhook endpoint."""
        adapter = CampfireAdapter(config)

        app = adapter._setup_routes()

        # Check that routes were registered
        routes = [r.resource.canonical for r in app.router.routes()]
        assert "/my-webhook" in routes

    def test_setup_routes_registers_health(self, config):
        """Test that setup_routes registers health endpoint."""
        adapter = CampfireAdapter(config)

        app = adapter._setup_routes()

        routes = [r.resource.canonical for r in app.router.routes()]
        assert "/health" in routes


class TestCampfireAdapterContext:
    """Tests for conversation context fetching."""

    @pytest.fixture
    def config(self):
        return CampfireConfig(
            base_url="https://chat.example.com",
            bot_key="123-abc123xyz456",
            context_messages=5,
        )

    @pytest.fixture
    def adapter(self, config):
        return CampfireAdapter(config)

    def test_build_read_messages_url(self, adapter):
        """Test building URL for reading messages."""
        url = adapter._build_read_messages_url("42")
        assert url == "https://chat.example.com/rooms/42/123-abc123xyz456/messages"

    @pytest.mark.asyncio
    async def test_fetch_room_context_success(self, adapter):
        """Test fetching room context successfully."""
        from aiohttp import ClientSession

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "room": {"id": 1, "name": "General"},
                "messages": [
                    {
                        "id": 10,
                        "body": {"plain": "Hello"},
                        "creator": {"id": 1, "name": "Alice", "is_bot": False},
                    },
                    {
                        "id": 11,
                        "body": {"plain": "Hi there"},
                        "creator": {"id": 2, "name": "Bob", "is_bot": False},
                    },
                ],
                "pagination": {},
            }
        )

        # Create a proper async context manager mock
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_response
        mock_cm.__aexit__.return_value = None

        adapter._http_session = MagicMock(spec=ClientSession)
        adapter._http_session.get.return_value = mock_cm

        messages = await adapter.fetch_room_context("1")

        # Messages should be reversed to chronological order
        assert len(messages) == 2
        assert messages[0]["body"]["plain"] == "Hi there"  # Reversed
        assert messages[1]["body"]["plain"] == "Hello"

    @pytest.mark.asyncio
    async def test_fetch_room_context_takes_most_recent(self, config):
        """Test that we take the most recent N messages, not the oldest N.

        When API returns more messages than context_messages limit, we should
        take the last N (most recent before the target) not the first N (oldest).
        """
        from aiohttp import ClientSession

        # Configure to only want 3 messages
        config.context_messages = 3
        adapter = CampfireAdapter(config)

        # API returns 5 messages (newest first): msg5, msg4, msg3, msg2, msg1
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={
                "room": {"id": 1, "name": "General"},
                "messages": [
                    {"id": 5, "body": {"plain": "msg5"}, "creator": {"name": "A"}},
                    {"id": 4, "body": {"plain": "msg4"}, "creator": {"name": "A"}},
                    {"id": 3, "body": {"plain": "msg3"}, "creator": {"name": "A"}},
                    {"id": 2, "body": {"plain": "msg2"}, "creator": {"name": "A"}},
                    {"id": 1, "body": {"plain": "msg1"}, "creator": {"name": "A"}},
                ],
                "pagination": {},
            }
        )

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_response
        mock_cm.__aexit__.return_value = None

        adapter._http_session = MagicMock(spec=ClientSession)
        adapter._http_session.get.return_value = mock_cm

        messages = await adapter.fetch_room_context("1")

        # Should get the 3 most recent (msg3, msg2, msg1 from API = last 3)
        # Reversed to chronological order: msg1, msg2, msg3
        assert len(messages) == 3
        assert messages[0]["body"]["plain"] == "msg1"  # Oldest of the 3
        assert messages[1]["body"]["plain"] == "msg2"
        assert messages[2]["body"]["plain"] == "msg3"  # Most recent (before target)

    @pytest.mark.asyncio
    async def test_fetch_room_context_404(self, adapter):
        """Test fetch returns empty list on 404 (bot read API not available)."""
        from aiohttp import ClientSession

        mock_response = AsyncMock()
        mock_response.status = 404

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_response
        mock_cm.__aexit__.return_value = None

        adapter._http_session = MagicMock(spec=ClientSession)
        adapter._http_session.get.return_value = mock_cm

        messages = await adapter.fetch_room_context("1")

        assert messages == []

    @pytest.mark.asyncio
    async def test_fetch_room_context_disabled(self, adapter):
        """Test fetch returns empty when context_messages is 0."""
        adapter._config.context_messages = 0
        adapter._http_session = AsyncMock()

        messages = await adapter.fetch_room_context("1")

        assert messages == []
        # Should not have made any HTTP requests
        adapter._http_session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_room_context_not_started(self, adapter):
        """Test fetch raises when adapter not started."""
        with pytest.raises(RuntimeError, match="not started"):
            await adapter.fetch_room_context("1")

    def test_format_context_for_prompt_empty(self, adapter):
        """Test formatting empty context."""
        result = adapter._format_context_for_prompt([], "Alice")
        assert result == ""

    def test_format_context_for_prompt_with_messages(self, adapter):
        """Test formatting context with messages."""
        messages = [
            {
                "body": {"plain": "Hello everyone"},
                "creator": {"id": 1, "name": "Alice", "is_bot": False},
            },
            {
                "body": {"plain": "Hi Alice!"},
                "creator": {"id": 2, "name": "PawBot", "is_bot": True},
            },
        ]

        result = adapter._format_context_for_prompt(messages, "Bob")

        assert "recent conversation context" in result
        assert "**Alice**: Hello everyone" in result
        assert "**PawBot (bot)**: Hi Alice!" in result
        assert "Now Bob says:" in result

    def test_config_context_messages_default(self):
        """Test context_messages has sensible default."""
        config = CampfireConfig(
            base_url="https://chat.example.com",
            bot_key="123-abc",
        )
        assert config.context_messages == 10
