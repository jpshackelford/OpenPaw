"""Tests for the conversation runner."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from openpaws.config import AgentConfig, Config, GroupConfig, TaskConfig
from openpaws.runner import ConversationResult, ConversationRunner
from openpaws.scheduler import ScheduledTask


@pytest.fixture
def sample_config():
    """Create a sample configuration for testing."""
    return Config(
        channels={},
        groups={
            "main": GroupConfig(
                name="main",
                channel="telegram",
                chat_id="123",
                trigger="@paw",
                admin=True,
            ),
            "family": GroupConfig(
                name="family",
                channel="telegram",
                chat_id="456",
                trigger="@paw",
                admin=False,
            ),
        },
        tasks={
            "morning": TaskConfig(
                name="morning",
                schedule="0 8 * * *",
                group="main",
                prompt="Good morning! What's the weather?",
            ),
        },
        agent=AgentConfig(
            model="anthropic/claude-sonnet-4-20250514",
            temperature=0.7,
        ),
    )


@pytest.fixture
def temp_base_dir():
    """Create a temporary base directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestConversationRunner:
    """Tests for ConversationRunner."""

    def test_init(self, sample_config, temp_base_dir):
        """Test runner initialization."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)
        assert runner.config == sample_config
        assert runner.base_dir == temp_base_dir

    def test_default_base_dir(self, sample_config):
        """Test default base directory."""
        runner = ConversationRunner(sample_config)
        assert runner.base_dir == Path.home() / ".openpaws"

    def test_get_api_key_anthropic(self, sample_config, temp_base_dir):
        """Test API key detection for Anthropic models."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            key = runner._get_api_key("anthropic/claude-sonnet-4-20250514")
            assert key == "test-key"

            key = runner._get_api_key("claude-3-opus")
            assert key == "test-key"

    def test_get_api_key_openai(self, sample_config, temp_base_dir):
        """Test API key detection for OpenAI models."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        with patch.dict(os.environ, {"OPENAI_API_KEY": "openai-key"}, clear=True):
            key = runner._get_api_key("openai/gpt-4")
            assert key == "openai-key"

            key = runner._get_api_key("gpt-4-turbo")
            assert key == "openai-key"

    def test_get_api_key_google(self, sample_config, temp_base_dir):
        """Test API key detection for Google models."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "google-key"}, clear=True):
            key = runner._get_api_key("gemini/gemini-pro")
            assert key == "google-key"

        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-key"}, clear=True):
            key = runner._get_api_key("google/gemini-pro")
            assert key == "gemini-key"

    def test_get_api_key_fallback(self, sample_config, temp_base_dir):
        """Test fallback API key."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        with patch.dict(os.environ, {"LLM_API_KEY": "fallback-key"}, clear=True):
            key = runner._get_api_key("some-other-model")
            assert key == "fallback-key"

    def test_get_group_workspace(self, sample_config, temp_base_dir):
        """Test workspace directory creation."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)
        group = sample_config.groups["main"]

        workspace = runner._get_group_workspace(group)

        assert workspace == temp_base_dir / "groups" / "main" / "workspace"
        assert workspace.exists()

    def test_get_group_persistence_dir(self, sample_config, temp_base_dir):
        """Test persistence directory creation."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)
        group = sample_config.groups["main"]

        persistence_dir = runner._get_group_persistence_dir(group)

        assert persistence_dir == temp_base_dir / "groups" / "main" / "sessions"
        assert persistence_dir.exists()

    def test_create_llm_basic(self, sample_config, temp_base_dir):
        """Test LLM creation with basic config."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        # Clear environment to ensure we use config values
        with patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "test-key", "LLM_MODEL": "", "LLM_BASE_URL": ""},
            clear=True,
        ):
            llm = runner._create_llm()

            assert llm.model == "anthropic/claude-sonnet-4-20250514"
            assert llm.temperature == 0.7

    def test_create_llm_with_proxy(self, temp_base_dir):
        """Test LLM creation with proxy."""
        config = Config(
            agent=AgentConfig(
                model="anthropic/claude-sonnet-4-20250514",
                llm_proxy="http://localhost:4000",
            ),
        )
        runner = ConversationRunner(config, base_dir=temp_base_dir)

        # Clear LLM_BASE_URL to ensure we use the config value
        env = {"ANTHROPIC_API_KEY": "test-key", "LLM_BASE_URL": ""}
        with patch.dict(os.environ, env, clear=True):
            llm = runner._create_llm()

            assert llm.base_url == "http://localhost:4000"


class TestConversationResult:
    """Tests for ConversationResult."""

    def test_success_result(self):
        """Test successful result."""
        result = ConversationResult(
            success=True,
            message="Hello!",
            events=[],
        )
        assert result.success is True
        assert result.message == "Hello!"
        assert result.error is None

    def test_failure_result(self):
        """Test failed result."""
        result = ConversationResult(
            success=False,
            message="Failed",
            error="Connection timeout",
        )
        assert result.success is False
        assert result.error == "Connection timeout"


class TestRunPrompt:
    """Tests for run_prompt method."""

    @pytest.mark.asyncio
    async def test_unknown_group(self, sample_config, temp_base_dir):
        """Test running prompt with unknown group."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        result = await runner.run_prompt("nonexistent", "Hello")

        assert result.success is False
        assert "not found" in result.message
        assert "nonexistent" in result.error


class TestRunTask:
    """Tests for run_task method."""

    @pytest.mark.asyncio
    async def test_run_task_calls_run_prompt(self, sample_config, temp_base_dir):
        """Test that run_task delegates to run_prompt."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        task = ScheduledTask(config=sample_config.tasks["morning"])

        # Mock run_prompt to verify it's called correctly
        with patch.object(runner, "run_prompt") as mock_run_prompt:
            mock_run_prompt.return_value = ConversationResult(
                success=True, message="Done"
            )

            await runner.run_task(task)

            mock_run_prompt.assert_called_once_with(
                group_name="main",
                prompt="Good morning! What's the weather?",
            )


class TestRunMessage:
    """Tests for run_message method."""

    @pytest.mark.asyncio
    async def test_run_message_calls_run_prompt(self, sample_config, temp_base_dir):
        """Test that run_message delegates to run_prompt."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        with patch.object(runner, "run_prompt") as mock_run_prompt:
            mock_run_prompt.return_value = ConversationResult(
                success=True, message="Response"
            )

            await runner.run_message("main", "Hello there!")

            mock_run_prompt.assert_called_once_with(
                group_name="main",
                prompt="Hello there!",
                conversation_id=None,
                send_callback=None,
            )


class TestAPIKeyDetection:
    """Additional tests for API key detection."""

    def test_get_api_key_no_match(self, sample_config, temp_base_dir):
        """Test API key returns None for unknown model without fallback."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        with patch.dict(os.environ, {}, clear=True):
            key = runner._get_api_key("some-unknown-model")
            assert key is None

    def test_get_api_key_generic_takes_precedence(self, sample_config, temp_base_dir):
        """Test LLM_API_KEY takes precedence over provider-specific keys."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        with patch.dict(
            os.environ,
            {"LLM_API_KEY": "generic-key", "ANTHROPIC_API_KEY": "anthropic-key"},
            clear=True,
        ):
            key = runner._get_api_key("anthropic/claude-3-opus")
            assert key == "generic-key"

    def test_get_model_from_env(self, sample_config, temp_base_dir):
        """Test model selection from environment variable."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        with patch.dict(os.environ, {"LLM_MODEL": "openai/gpt-4"}, clear=True):
            model = runner._get_model()
            assert model == "openai/gpt-4"

    def test_get_model_from_config(self, sample_config, temp_base_dir):
        """Test model selection from config when env not set."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        with patch.dict(os.environ, {}, clear=True):
            model = runner._get_model()
            assert model == "anthropic/claude-sonnet-4-20250514"

    def test_get_base_url_from_env(self, sample_config, temp_base_dir):
        """Test base URL from environment variable."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        with patch.dict(os.environ, {"LLM_BASE_URL": "http://proxy.local"}, clear=True):
            url = runner._get_base_url()
            assert url == "http://proxy.local"

    def test_get_base_url_from_config(self, temp_base_dir):
        """Test base URL from config when env not set."""
        config = Config(
            agent=AgentConfig(
                model="test-model",
                llm_proxy="http://config-proxy.local",
            ),
        )
        runner = ConversationRunner(config, base_dir=temp_base_dir)

        with patch.dict(os.environ, {}, clear=True):
            url = runner._get_base_url()
            assert url == "http://config-proxy.local"

    def test_get_base_url_none(self, sample_config, temp_base_dir):
        """Test base URL returns None when not configured."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        with patch.dict(os.environ, {}, clear=True):
            url = runner._get_base_url()
            assert url is None


class TestBuildLLMKwargs:
    """Tests for _build_llm_kwargs method."""

    def test_build_llm_kwargs_minimal(self, sample_config, temp_base_dir):
        """Test building LLM kwargs with minimal config."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        with patch.dict(os.environ, {}, clear=True):
            kwargs = runner._build_llm_kwargs()

            assert "model" in kwargs
            assert kwargs["model"] == "anthropic/claude-sonnet-4-20250514"
            assert kwargs.get("temperature") == 0.7

    def test_build_llm_kwargs_with_api_key(self, sample_config, temp_base_dir):
        """Test building LLM kwargs includes API key."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            kwargs = runner._build_llm_kwargs()

            assert "api_key" in kwargs
            assert kwargs["api_key"].get_secret_value() == "test-key"

    def test_build_llm_kwargs_with_max_tokens(self, temp_base_dir):
        """Test building LLM kwargs includes max_tokens."""
        config = Config(
            agent=AgentConfig(
                model="test-model",
                max_tokens=4096,
            ),
        )
        runner = ConversationRunner(config, base_dir=temp_base_dir)

        with patch.dict(os.environ, {}, clear=True):
            kwargs = runner._build_llm_kwargs()

            assert kwargs.get("max_output_tokens") == 4096


class TestDefaultTools:
    """Tests for _get_default_tools method."""

    def test_get_default_tools_includes_send_status(self, sample_config, temp_base_dir):
        """Test that default tools include SendStatusTool."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        tools = runner._get_default_tools()

        tool_names = [t.name for t in tools]
        assert "send_status" in tool_names


class TestCustomInstructions:
    """Tests for _build_custom_instructions method."""

    def test_build_custom_instructions_default(self, sample_config, temp_base_dir):
        """Test building default custom instructions."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        instructions = runner._build_custom_instructions()

        assert "send_status" in instructions.lower()

    def test_build_custom_instructions_with_system_prompt(self, temp_base_dir):
        """Test building custom instructions with system prompt."""
        config = Config(
            agent=AgentConfig(
                model="test-model",
                system_prompt="You are a helpful assistant.",
            ),
        )
        runner = ConversationRunner(config, base_dir=temp_base_dir)

        instructions = runner._build_custom_instructions()

        assert "You are a helpful assistant" in instructions
        assert "send_status" in instructions.lower()


class TestExtractFinalResponse:
    """Tests for _extract_final_response method."""

    def test_extract_no_response(self, sample_config, temp_base_dir):
        """Test extracting response when no events."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        response = runner._extract_final_response([])

        assert response == "No response generated"

    def test_extract_finish_action_returns_none(self, sample_config, temp_base_dir):
        """Test _extract_finish_action_message returns None for non-ActionEvent."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        # Mock a non-ActionEvent
        from unittest.mock import MagicMock

        mock_event = MagicMock()
        mock_event.__class__.__name__ = "SomeOtherEvent"

        result = runner._extract_finish_action_message(mock_event)
        assert result is None

    def test_extract_assistant_message_returns_none(self, sample_config, temp_base_dir):
        """Test _extract_assistant_message returns None for non-MessageEvent."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        from unittest.mock import MagicMock

        mock_event = MagicMock()
        mock_event.__class__.__name__ = "SomeOtherEvent"

        result = runner._extract_assistant_message(mock_event)
        assert result is None


class TestMakeEventCollector:
    """Tests for _make_event_collector method."""

    def test_make_event_collector(self, sample_config, temp_base_dir):
        """Test event collector callback."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)
        events = []

        callback = runner._make_event_collector(events)

        # Simulate calling the callback with events
        callback("event1")
        callback("event2")

        assert events == ["event1", "event2"]


class TestBuildCallbacks:
    """Tests for _build_callbacks method."""

    def test_build_callbacks_no_extra(self, sample_config, temp_base_dir):
        """Test building callbacks without extra callbacks."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)
        events = []

        callbacks = runner._build_callbacks(events, None)

        assert len(callbacks) == 1  # Just the event collector

    def test_build_callbacks_with_extra(self, sample_config, temp_base_dir):
        """Test building callbacks with extra callbacks."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)
        events = []
        extra = [lambda x: None, lambda x: None]

        callbacks = runner._build_callbacks(events, extra)

        assert len(callbacks) == 3  # Event collector + 2 extra


class TestGroupNotFoundResult:
    """Tests for _group_not_found_result method."""

    def test_group_not_found_result(self, sample_config, temp_base_dir):
        """Test group not found result format."""
        runner = ConversationRunner(sample_config, base_dir=temp_base_dir)

        result = runner._group_not_found_result("missing-group")

        assert result.success is False
        assert "missing-group" in result.message
        assert "missing-group" in result.error


class TestRemoteServerMode:
    """Tests for remote server mode in ConversationRunner."""

    def test_use_remote_servers_disabled_by_default(self):
        """Test that use_remote_servers is False by default."""
        from openpaws.config import Config
        from openpaws.runner import ConversationRunner

        config = Config()
        runner = ConversationRunner(config)
        assert runner.use_remote_servers is False

    def test_use_remote_servers_false_without_manager(self):
        """Test use_remote_servers is False when config enabled but no manager."""
        from openpaws.config import Config, RemoteServerConfig
        from openpaws.runner import ConversationRunner

        config = Config(remote_servers=RemoteServerConfig(enabled=True))
        runner = ConversationRunner(config)
        assert runner.use_remote_servers is False

    def test_use_remote_servers_false_when_config_disabled(self):
        """Test use_remote_servers is False when config disabled with manager."""
        from unittest.mock import MagicMock

        from openpaws.config import Config, RemoteServerConfig
        from openpaws.runner import ConversationRunner

        config = Config(remote_servers=RemoteServerConfig(enabled=False))
        mock_manager = MagicMock()
        runner = ConversationRunner(config, server_manager=mock_manager)
        assert runner.use_remote_servers is False

    def test_use_remote_servers_true_when_enabled_and_manager_present(self):
        """Test use_remote_servers is True when enabled with manager."""
        from unittest.mock import MagicMock

        from openpaws.config import Config, RemoteServerConfig
        from openpaws.runner import ConversationRunner

        config = Config(remote_servers=RemoteServerConfig(enabled=True))
        mock_manager = MagicMock()
        runner = ConversationRunner(config, server_manager=mock_manager)
        assert runner.use_remote_servers is True

    def test_server_manager_stored_correctly(self):
        """Test that server_manager is stored on the runner."""
        from unittest.mock import MagicMock

        from openpaws.config import Config
        from openpaws.runner import ConversationRunner

        config = Config()
        mock_manager = MagicMock()
        runner = ConversationRunner(config, server_manager=mock_manager)
        assert runner._server_manager is mock_manager


class TestRemoteServerMethods:
    """Tests for remote server execution methods."""

    @pytest.fixture
    def runner_with_manager(self, sample_config, temp_base_dir):
        """Create a runner with a mock server manager."""
        from unittest.mock import AsyncMock, MagicMock

        from openpaws.config import RemoteServerConfig

        sample_config.remote_servers = RemoteServerConfig(enabled=True)
        mock_manager = MagicMock()
        mock_manager.get_or_create_server = AsyncMock()
        mock_manager.start_conversation = AsyncMock()
        mock_manager.send_message = AsyncMock()
        mock_manager.run_conversation = AsyncMock()
        mock_manager.get_conversation_status = AsyncMock()
        runner = ConversationRunner(
            sample_config, base_dir=temp_base_dir, server_manager=mock_manager
        )
        return runner, mock_manager

    @pytest.mark.asyncio
    async def test_setup_remote_conversation(self, runner_with_manager, sample_config):
        """Test _setup_remote_conversation sets up server and returns conv_id."""
        from unittest.mock import MagicMock

        runner, mock_manager = runner_with_manager
        mock_server = MagicMock(port=18000)
        mock_manager.get_or_create_server.return_value = mock_server
        mock_manager.start_conversation.return_value = "conv-123"

        group = sample_config.groups["main"]
        conv_id = await runner._setup_remote_conversation(group)

        assert conv_id == "conv-123"
        mock_manager.get_or_create_server.assert_called_once_with(group.name)
        mock_manager.start_conversation.assert_called_once()

    def test_handle_remote_error(self, runner_with_manager):
        """Test _handle_remote_error returns proper ConversationResult."""
        runner, _ = runner_with_manager

        result = runner._handle_remote_error("test-group", ValueError("test error"))

        assert result.success is False
        assert "test error" in result.message
        assert "test error" in result.error

    def test_build_agent_config(self, runner_with_manager):
        """Test _build_agent_config returns proper config dict."""
        runner, _ = runner_with_manager

        config = runner._build_agent_config()

        assert "model" in config
        assert "temperature" in config
        assert "max_tokens" in config
        assert "system_prompt" in config

    @pytest.mark.asyncio
    async def test_check_remote_status_not_found(self, runner_with_manager):
        """Test _check_remote_status returns error when status is None."""
        runner, mock_manager = runner_with_manager
        mock_manager.get_conversation_status.return_value = None

        result = await runner._check_remote_status("test-group")

        assert result == "Conversation not found"

    @pytest.mark.asyncio
    async def test_check_remote_status_completed(self, runner_with_manager):
        """Test _check_remote_status returns response when completed."""
        runner, mock_manager = runner_with_manager
        mock_manager.get_conversation_status.return_value = "completed"

        result = await runner._check_remote_status("test-group")

        assert "completed" in result.lower() or "not yet implemented" in result.lower()

    @pytest.mark.asyncio
    async def test_check_remote_status_error(self, runner_with_manager):
        """Test _check_remote_status returns error message on error status."""
        runner, mock_manager = runner_with_manager
        mock_manager.get_conversation_status.return_value = "error"

        result = await runner._check_remote_status("test-group")

        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_check_remote_status_running(self, runner_with_manager):
        """Test _check_remote_status returns None when still running."""
        runner, mock_manager = runner_with_manager
        mock_manager.get_conversation_status.return_value = "running"

        result = await runner._check_remote_status("test-group")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_remote_response(self, runner_with_manager):
        """Test _get_remote_response returns placeholder response."""
        runner, _ = runner_with_manager

        result = await runner._get_remote_response("test-group")

        assert "not yet implemented" in result.lower()

    @pytest.mark.asyncio
    async def test_execute_prompt_remote_success(
        self, runner_with_manager, sample_config
    ):
        """Test _execute_prompt_remote successful flow."""
        from unittest.mock import MagicMock

        runner, mock_manager = runner_with_manager
        mock_server = MagicMock(port=18000)
        mock_manager.get_or_create_server.return_value = mock_server
        mock_manager.start_conversation.return_value = "conv-123"
        mock_manager.get_conversation_status.return_value = "completed"

        group = sample_config.groups["main"]
        result = await runner._execute_prompt_remote(group, "test prompt", None)

        assert result.success is True
        mock_manager.send_message.assert_called_once()
        mock_manager.run_conversation.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_prompt_remote_error(
        self, runner_with_manager, sample_config
    ):
        """Test _execute_prompt_remote handles errors."""
        runner, mock_manager = runner_with_manager
        mock_manager.get_or_create_server.side_effect = Exception("Connection failed")

        group = sample_config.groups["main"]
        result = await runner._execute_prompt_remote(group, "test prompt", None)

        assert result.success is False
        assert "Connection failed" in result.error
