"""Tests for SendStatusTool and related functionality."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openpaws.channels.base import ChannelContext
from openpaws.tools.send_status import (
    SendStatusAction,
    SendStatusExecutor,
    SendStatusObservation,
    SendStatusTool,
    _run_async,
    _run_async_callback,
    get_send_callback,
    register_send_callback,
    unregister_send_callback,
)


class TestCallbackRegistry:
    """Tests for callback registration functions."""

    def test_register_and_get_callback(self):
        """Test registering and retrieving a callback."""

        async def mock_callback(msg: str) -> None:
            pass

        conv_id = "test-conv-123"
        register_send_callback(conv_id, mock_callback)

        retrieved = get_send_callback(conv_id)
        assert retrieved is mock_callback

        # Clean up
        unregister_send_callback(conv_id)

    def test_get_unregistered_callback(self):
        """Test getting a callback that was never registered."""
        result = get_send_callback("nonexistent-conv")
        assert result is None

    def test_unregister_callback(self):
        """Test unregistering a callback."""

        async def mock_callback(msg: str) -> None:
            pass

        conv_id = "test-conv-456"
        register_send_callback(conv_id, mock_callback)
        assert get_send_callback(conv_id) is not None

        unregister_send_callback(conv_id)
        assert get_send_callback(conv_id) is None

    def test_unregister_nonexistent_callback(self):
        """Test unregistering a callback that doesn't exist (should not raise)."""
        unregister_send_callback("never-registered")  # Should not raise


class TestSendStatusAction:
    """Tests for SendStatusAction."""

    def test_create_action(self):
        """Test creating a SendStatusAction."""
        action = SendStatusAction(message="Working on it!")
        assert action.message == "Working on it!"

    def test_visualize(self):
        """Test the visualize property returns Rich Text."""
        action = SendStatusAction(message="Processing your request...")
        visual = action.visualize
        assert "📤" in str(visual)
        assert "Processing your request..." in str(visual)


class TestSendStatusObservation:
    """Tests for SendStatusObservation."""

    def test_observation_sent_true(self):
        """Test observation when message was sent."""
        obs = SendStatusObservation.from_text(text="Message sent", sent=True)
        assert obs.sent is True
        visual = obs.visualize
        assert "✓" in str(visual)

    def test_observation_sent_false(self):
        """Test observation when message was not sent."""
        obs = SendStatusObservation.from_text(text="Not sent", sent=False)
        assert obs.sent is False
        visual = obs.visualize
        assert "✗" in str(visual)
        assert "Failed" in str(visual)


class TestRunAsyncCallback:
    """Tests for _run_async_callback helper."""

    def test_run_async_callback(self):
        """Test running an async callback."""
        messages_received = []

        async def capture_callback(msg: str) -> None:
            messages_received.append(msg)

        _run_async_callback(capture_callback, "Hello!")
        assert messages_received == ["Hello!"]


class TestSendStatusExecutor:
    """Tests for SendStatusExecutor."""

    def test_executor_no_conversation(self):
        """Test executor when conversation is None."""
        executor = SendStatusExecutor()
        action = SendStatusAction(message="Test message")

        result = executor(action, conversation=None)

        assert isinstance(result, SendStatusObservation)
        assert result.sent is False
        assert "no channel configured" in result.text.lower()

    def test_executor_conversation_without_state(self):
        """Test executor when conversation has no state attribute."""
        executor = SendStatusExecutor()
        action = SendStatusAction(message="Test message")
        mock_conv = MagicMock(spec=[])  # No attributes

        result = executor(action, conversation=mock_conv)

        assert result.sent is False

    def test_executor_with_registered_callback(self):
        """Test executor when callback is registered."""
        messages_sent = []

        async def mock_send(msg: str) -> None:
            messages_sent.append(msg)

        # Create a mock conversation with state.id
        mock_state = MagicMock()
        mock_state.id = "exec-test-conv"
        mock_conv = MagicMock()
        mock_conv.state = mock_state

        # Register the callback
        register_send_callback("exec-test-conv", mock_send)

        try:
            executor = SendStatusExecutor()
            action = SendStatusAction(message="I'm working on it!")

            result = executor(action, conversation=mock_conv)

            assert result.sent is True
            assert "I'm working on it!" in messages_sent
        finally:
            unregister_send_callback("exec-test-conv")

    def test_get_callback_returns_none_without_state(self):
        """Test _get_callback returns None when conversation lacks state."""
        executor = SendStatusExecutor()

        # Conversation with no state
        mock_conv = MagicMock()
        del mock_conv.state  # Remove the attribute

        result = executor._get_callback(mock_conv)
        assert result is None


class TestSendStatusExecutorDirectPosting:
    """Tests for SendStatusExecutor direct posting functionality."""

    def test_get_channel_context_no_conversation(self):
        """Test _get_channel_context returns None without conversation."""
        executor = SendStatusExecutor()
        result = executor._get_channel_context(None)
        assert result is None

    def test_get_channel_context_no_state(self):
        """Test _get_channel_context returns None without state attribute."""
        executor = SendStatusExecutor()
        mock_conv = MagicMock(spec=[])  # No state attribute
        result = executor._get_channel_context(mock_conv)
        assert result is None

    def test_get_channel_context_no_agent_state(self):
        """Test _get_channel_context returns None without agent_state."""
        executor = SendStatusExecutor()
        mock_conv = MagicMock()
        mock_conv.state = MagicMock()
        mock_conv.state.agent_state = None
        result = executor._get_channel_context(mock_conv)
        assert result is None

    def test_get_channel_context_no_channel_context_key(self):
        """Test _get_channel_context returns None without channel_context."""
        executor = SendStatusExecutor()
        mock_conv = MagicMock()
        mock_conv.state = MagicMock()
        mock_conv.state.agent_state = {}
        result = executor._get_channel_context(mock_conv)
        assert result is None

    def test_get_channel_context_valid(self):
        """Test _get_channel_context returns ChannelContext when valid."""
        executor = SendStatusExecutor()
        mock_conv = MagicMock()
        mock_conv.state = MagicMock()
        mock_conv.state.agent_state = {
            "channel_context": {
                "channel_type": "campfire",
                "channel_id": "room123",
                "thread_id": None,
                "base_url": "https://example.37signals.com",
                "credential_key": "CAMPFIRE_BOT_KEY",
            }
        }
        result = executor._get_channel_context(mock_conv)
        assert result is not None
        assert isinstance(result, ChannelContext)
        assert result.channel_type == "campfire"
        assert result.channel_id == "room123"
        assert result.base_url == "https://example.37signals.com"

    def test_get_channel_context_invalid_data(self):
        """Test _get_channel_context handles invalid data gracefully."""
        executor = SendStatusExecutor()
        mock_conv = MagicMock()
        mock_conv.state = MagicMock()
        # Missing required field 'channel_id'
        mock_conv.state.agent_state = {"channel_context": {"channel_type": "slack"}}
        result = executor._get_channel_context(mock_conv)
        assert result is None

    def test_get_credential_no_conversation(self):
        """Test _get_credential returns None without conversation."""
        executor = SendStatusExecutor()
        result = executor._get_credential(None, "TEST_KEY")
        assert result is None

    def test_get_credential_no_state(self):
        """Test _get_credential returns None without state."""
        executor = SendStatusExecutor()
        mock_conv = MagicMock(spec=[])
        result = executor._get_credential(mock_conv, "TEST_KEY")
        assert result is None

    def test_get_credential_no_secret_registry(self):
        """Test _get_credential returns None without secret_registry."""
        executor = SendStatusExecutor()
        mock_conv = MagicMock()
        mock_conv.state = MagicMock()
        mock_conv.state.secret_registry = None
        result = executor._get_credential(mock_conv, "TEST_KEY")
        assert result is None

    def test_get_credential_valid(self):
        """Test _get_credential returns credential when available."""
        executor = SendStatusExecutor()
        mock_conv = MagicMock()
        mock_conv.state = MagicMock()
        mock_conv.state.secret_registry = MagicMock()
        mock_conv.state.secret_registry.get_secrets.return_value = {
            "BOT_KEY": "secret-token-123"
        }
        result = executor._get_credential(mock_conv, "BOT_KEY")
        assert result == "secret-token-123"

    def test_try_direct_post_no_context(self):
        """Test _try_direct_post returns None without channel context."""
        executor = SendStatusExecutor()
        action = SendStatusAction(message="Test")
        mock_conv = MagicMock()
        mock_conv.state = MagicMock()
        mock_conv.state.agent_state = {}

        result = executor._try_direct_post(action, mock_conv)
        assert result is None

    def test_try_direct_post_no_credential(self):
        """Test _try_direct_post returns None when credential is missing."""
        executor = SendStatusExecutor()
        action = SendStatusAction(message="Test")
        mock_conv = MagicMock()
        mock_conv.state = MagicMock()
        mock_conv.state.agent_state = {
            "channel_context": {
                "channel_type": "slack",
                "channel_id": "C123",
                "credential_key": "SLACK_TOKEN",
            }
        }
        mock_conv.state.secret_registry = MagicMock()
        mock_conv.state.secret_registry.get_secrets.return_value = {}

        result = executor._try_direct_post(action, mock_conv)
        assert result is None

    def test_try_direct_post_success(self):
        """Test _try_direct_post returns observation on success."""
        executor = SendStatusExecutor()
        action = SendStatusAction(message="Working on it!")
        mock_conv = MagicMock()
        mock_conv.state = MagicMock()
        mock_conv.state.agent_state = {
            "channel_context": {
                "channel_type": "slack",
                "channel_id": "C123",
                "credential_key": "SLACK_TOKEN",
            }
        }
        mock_conv.state.secret_registry = MagicMock()
        mock_conv.state.secret_registry.get_secrets.return_value = {
            "SLACK_TOKEN": "xoxb-token"
        }

        with patch(
            "openpaws.tools.channel_poster.post_to_channel", new_callable=AsyncMock
        ) as mock_post:
            mock_post.return_value = True

            result = executor._try_direct_post(action, mock_conv)

            assert result is not None
            assert result.sent is True
            mock_post.assert_called_once()

    def test_try_direct_post_failure(self):
        """Test _try_direct_post returns None when posting fails."""
        executor = SendStatusExecutor()
        action = SendStatusAction(message="Working on it!")
        mock_conv = MagicMock()
        mock_conv.state = MagicMock()
        mock_conv.state.agent_state = {
            "channel_context": {
                "channel_type": "slack",
                "channel_id": "C123",
                "credential_key": "SLACK_TOKEN",
            }
        }
        mock_conv.state.secret_registry = MagicMock()
        mock_conv.state.secret_registry.get_secrets.return_value = {
            "SLACK_TOKEN": "xoxb-token"
        }

        with patch(
            "openpaws.tools.channel_poster.post_to_channel", new_callable=AsyncMock
        ) as mock_post:
            mock_post.return_value = False

            result = executor._try_direct_post(action, mock_conv)

            assert result is None  # Falls back to callback

    def test_try_direct_post_exception(self):
        """Test _try_direct_post returns None when exception occurs."""
        executor = SendStatusExecutor()
        action = SendStatusAction(message="Working on it!")
        mock_conv = MagicMock()
        mock_conv.state = MagicMock()
        mock_conv.state.agent_state = {
            "channel_context": {
                "channel_type": "slack",
                "channel_id": "C123",
                "credential_key": "SLACK_TOKEN",
            }
        }
        mock_conv.state.secret_registry = MagicMock()
        mock_conv.state.secret_registry.get_secrets.return_value = {
            "SLACK_TOKEN": "xoxb-token"
        }

        with patch(
            "openpaws.tools.channel_poster.post_to_channel", new_callable=AsyncMock
        ) as mock_post:
            mock_post.side_effect = Exception("Network error")

            result = executor._try_direct_post(action, mock_conv)

            assert result is None

    def test_executor_direct_post_priority_over_callback(self):
        """Test that direct posting is tried before callback."""
        messages_sent = []

        async def mock_send(msg: str) -> None:
            messages_sent.append(msg)

        mock_conv = MagicMock()
        mock_conv.state = MagicMock()
        mock_conv.state.id = "test-conv"
        mock_conv.state.agent_state = {
            "channel_context": {
                "channel_type": "slack",
                "channel_id": "C123",
                "credential_key": "SLACK_TOKEN",
            }
        }
        mock_conv.state.secret_registry = MagicMock()
        mock_conv.state.secret_registry.get_secrets.return_value = {
            "SLACK_TOKEN": "xoxb-token"
        }

        # Register a callback
        register_send_callback("test-conv", mock_send)

        try:
            executor = SendStatusExecutor()
            action = SendStatusAction(message="Test direct!")

            with patch(
                "openpaws.tools.channel_poster.post_to_channel", new_callable=AsyncMock
            ) as mock_post:
                mock_post.return_value = True

                result = executor(action, conversation=mock_conv)

                # Direct post should succeed
                assert result.sent is True
                # Callback should NOT have been called
                assert messages_sent == []
        finally:
            unregister_send_callback("test-conv")


class TestRunAsync:
    """Tests for _run_async helper."""

    def test_run_async_simple_coroutine(self):
        """Test running a simple coroutine."""

        async def simple_coro():
            return 42

        result = _run_async(simple_coro())
        assert result == 42


class TestSendStatusTool:
    """Tests for SendStatusTool."""

    def test_create_tool(self):
        """Test creating a SendStatusTool instance."""
        tools = SendStatusTool.create()

        assert len(tools) == 1
        tool = tools[0]
        assert tool.description == SendStatusTool.create()[0].description
        assert tool.action_type == SendStatusAction
        assert tool.observation_type == SendStatusObservation

    def test_create_tool_with_conv_state(self):
        """Test creating tool with conv_state parameter."""
        mock_state = MagicMock()
        tools = SendStatusTool.create(conv_state=mock_state)

        assert len(tools) == 1

    def test_create_tool_rejects_unknown_params(self):
        """Test that create() raises for unknown parameters."""
        with pytest.raises(ValueError) as exc_info:
            SendStatusTool.create(unknown_param="value")

        assert "doesn't accept" in str(exc_info.value)
        assert "unknown_param" in str(exc_info.value)

    def test_annotations(self):
        """Test tool annotations are set correctly."""
        tools = SendStatusTool.create()
        tool = tools[0]

        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is False
        assert tool.annotations.openWorldHint is True

    def test_tool_name(self):
        """Test that tool has a name attribute."""
        # The name comes from the class name transformation
        assert hasattr(SendStatusTool, "name")
