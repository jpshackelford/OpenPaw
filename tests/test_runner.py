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
        with patch.dict(
            os.environ, {"ANTHROPIC_API_KEY": "test-key", "LLM_BASE_URL": ""}, clear=True
        ):
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
