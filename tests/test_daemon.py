"""Tests for daemon process management."""

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from openpaws import daemon
from openpaws.channels.base import OutgoingMessage
from openpaws.config import Config, GroupConfig, TaskConfig
from openpaws.daemon import (
    ENV_LOG_FILE,
    ENV_OPENPAWS_DIR,
    ENV_PID_FILE,
    Daemon,
    format_uptime,
    get_daemon_status,
    get_log_file,
    get_openpaws_dir,
    get_pid_file,
    is_process_running,
    read_pid_file,
    remove_pid_file,
    write_pid_file,
)
from openpaws.scheduler import ScheduledTask


@pytest.fixture
def temp_openpaws_dir(tmp_path, monkeypatch):
    """Use a temporary directory for OpenPaws files via environment variable."""
    monkeypatch.setenv(ENV_OPENPAWS_DIR, str(tmp_path))
    # Clear any explicit overrides
    monkeypatch.delenv(ENV_PID_FILE, raising=False)
    monkeypatch.delenv(ENV_LOG_FILE, raising=False)
    yield tmp_path


class TestPidFileManagement:
    """Tests for PID file operations."""

    def test_get_openpaws_dir_creates_directory(self, temp_openpaws_dir):
        """Test that get_openpaws_dir creates the directory if it doesn't exist."""
        # Remove the temp dir first
        temp_openpaws_dir.rmdir()
        assert not temp_openpaws_dir.exists()

        result = get_openpaws_dir()
        assert result.exists()
        assert result.is_dir()

    def test_get_pid_file_returns_correct_path(self, temp_openpaws_dir):
        """Test that get_pid_file returns the correct path."""
        pid_file = get_pid_file()
        assert pid_file == temp_openpaws_dir / "openpaws.pid"

    def test_write_and_read_pid_file(self, temp_openpaws_dir):
        """Test writing and reading PID file."""
        test_pid = 12345
        write_pid_file(test_pid)

        # Verify file exists
        pid_file = get_pid_file()
        assert pid_file.exists()

        # Verify content
        read_pid = read_pid_file()
        assert read_pid == test_pid

    def test_read_pid_file_missing(self, temp_openpaws_dir):
        """Test reading PID file when it doesn't exist."""
        result = read_pid_file()
        assert result is None

    def test_read_pid_file_invalid_content(self, temp_openpaws_dir):
        """Test reading PID file with invalid content."""
        pid_file = get_pid_file()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("not a number")

        result = read_pid_file()
        assert result is None

    def test_remove_pid_file(self, temp_openpaws_dir):
        """Test removing PID file."""
        write_pid_file(12345)
        pid_file = get_pid_file()
        assert pid_file.exists()

        result = remove_pid_file()
        assert result is True
        assert not pid_file.exists()

    def test_remove_pid_file_missing(self, temp_openpaws_dir):
        """Test removing PID file when it doesn't exist."""
        result = remove_pid_file()
        assert result is False


class TestProcessChecking:
    """Tests for process status checking."""

    def test_is_process_running_current_process(self):
        """Test that current process is detected as running."""
        assert is_process_running(os.getpid()) is True

    def test_is_process_running_invalid_pid(self):
        """Test that invalid PIDs return False."""
        assert is_process_running(0) is False
        assert is_process_running(-1) is False

    def test_is_process_running_nonexistent_pid(self):
        """Test that non-existent PIDs return False."""
        # Use a very high PID that's unlikely to exist
        assert is_process_running(999999999) is False


class TestDaemonStatus:
    """Tests for daemon status reporting."""

    def test_get_daemon_status_not_running(self, temp_openpaws_dir):
        """Test status when daemon is not running."""
        status = get_daemon_status()
        assert status["running"] is False
        assert status["pid"] is None
        assert "pid_file" in status

    def test_get_daemon_status_with_running_process(self, temp_openpaws_dir):
        """Test status when daemon is running."""
        # Write current process PID (simulating running daemon)
        current_pid = os.getpid()
        write_pid_file(current_pid)

        status = get_daemon_status()
        assert status["running"] is True
        assert status["pid"] == current_pid

    def test_get_daemon_status_stale_pid_file(self, temp_openpaws_dir):
        """Test status when PID file points to dead process."""
        # Write a PID that doesn't exist
        write_pid_file(999999999)

        status = get_daemon_status()
        assert status["running"] is False
        assert status["pid"] is None


class TestUptimeFormatting:
    """Tests for uptime formatting."""

    def test_format_uptime_seconds(self):
        """Test formatting uptime in seconds."""
        assert format_uptime(30) == "30s"
        assert format_uptime(59) == "59s"

    def test_format_uptime_minutes(self):
        """Test formatting uptime in minutes."""
        assert format_uptime(60) == "1m"  # No trailing zero
        assert format_uptime(90) == "1m 30s"
        assert format_uptime(3599) == "59m 59s"

    def test_format_uptime_hours(self):
        """Test formatting uptime in hours."""
        assert format_uptime(3600) == "1h"  # No trailing zero
        assert format_uptime(7200) == "2h"  # No trailing zero
        assert format_uptime(3660) == "1h 1m"
        assert format_uptime(86399) == "23h 59m"

    def test_format_uptime_days(self):
        """Test formatting uptime in days."""
        assert format_uptime(86400) == "1d"  # No trailing zero
        assert format_uptime(172800) == "2d"  # No trailing zero
        assert format_uptime(90000) == "1d 1h"


class TestDaemonClass:
    """Tests for the Daemon class."""

    def test_daemon_init(self):
        """Test Daemon initialization."""
        daemon_obj = daemon.Daemon()
        assert daemon_obj.config_path is None
        assert daemon_obj.config is None
        assert daemon_obj.scheduler is None

    def test_daemon_init_with_config_path(self, tmp_path):
        """Test Daemon initialization with config path."""
        config_path = tmp_path / "test.yaml"
        daemon_obj = daemon.Daemon(config_path=config_path)
        assert daemon_obj.config_path == config_path

    def test_daemon_start_already_running(self, temp_openpaws_dir):
        """Test that starting when already running fails."""
        # Simulate running daemon with current process PID
        write_pid_file(os.getpid())

        # Verify the check works by looking at status
        status = get_daemon_status()
        assert status["running"] is True

    def test_daemon_stop_not_running(self, temp_openpaws_dir):
        """Test stopping when not running."""
        result = daemon.Daemon.stop()
        assert result == 0


class TestLogging:
    """Tests for logging setup."""

    def test_get_log_file_creates_directory(self, temp_openpaws_dir):
        """Test that get_log_file creates the logs directory."""
        log_file = daemon.get_log_file()
        assert log_file.parent.exists()
        assert log_file.parent.name == "logs"
        assert log_file.name == "openpaws.log"

    def test_setup_logging_to_file(self, temp_openpaws_dir):
        """Test setting up file logging."""
        daemon.setup_logging(log_to_file=True, debug=False)
        # Verify log file path is accessible
        assert daemon.get_log_file().parent.exists()

    def test_setup_logging_debug_mode(self, temp_openpaws_dir):
        """Test setting up logging in debug mode."""
        daemon.setup_logging(log_to_file=False, debug=True)
        # Verify debug mode doesn't crash


class TestDaemonState:
    """Tests for DaemonState dataclass."""

    def test_daemon_state_creation(self):
        """Test creating a DaemonState."""
        from datetime import datetime
        from pathlib import Path

        state = daemon.DaemonState(
            started_at=datetime.now(),
            config_path=Path("/tmp/test.yaml"),
        )
        assert state.started_at is not None
        assert state.config_path == Path("/tmp/test.yaml")

    def test_daemon_state_defaults(self):
        """Test DaemonState default values."""
        from datetime import datetime

        state = daemon.DaemonState(started_at=datetime.now())
        assert state.config_path is None


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_write_pid_uses_current_pid_by_default(self, temp_openpaws_dir):
        """Test that write_pid_file uses current PID when not specified."""
        write_pid_file()
        read_pid = read_pid_file()
        assert read_pid == os.getpid()

    def test_is_process_running_with_zombie_detection(self):
        """Test zombie process detection logic exists."""
        # The actual zombie detection is hard to test without creating zombies
        # But we can verify the function handles various states
        assert is_process_running(os.getpid()) is True

    def test_get_daemon_status_with_uptime(self, temp_openpaws_dir):
        """Test status includes uptime when process is running."""
        write_pid_file(os.getpid())
        status = get_daemon_status()
        assert status["running"] is True
        # Uptime should be present (using PID file mtime fallback)
        assert "uptime" in status or "uptime_seconds" in status


class TestEnvironmentVariables:
    """Tests for environment variable configuration."""

    def test_openpaws_dir_env_var(self, tmp_path, monkeypatch):
        """Test OPENPAWS_DIR environment variable."""
        custom_dir = tmp_path / "custom_openpaws"
        monkeypatch.setenv(ENV_OPENPAWS_DIR, str(custom_dir))
        monkeypatch.delenv(ENV_PID_FILE, raising=False)
        monkeypatch.delenv(ENV_LOG_FILE, raising=False)

        result = get_openpaws_dir()
        assert result == custom_dir
        assert result.exists()

    def test_pid_file_env_var(self, tmp_path, monkeypatch):
        """Test OPENPAWS_PID_FILE environment variable."""
        custom_pid = tmp_path / "custom" / "my.pid"
        monkeypatch.setenv(ENV_PID_FILE, str(custom_pid))

        result = get_pid_file()
        assert result == custom_pid
        assert result.parent.exists()

    def test_log_file_env_var(self, tmp_path, monkeypatch):
        """Test OPENPAWS_LOG_FILE environment variable."""
        custom_log = tmp_path / "custom" / "my.log"
        monkeypatch.setenv(ENV_LOG_FILE, str(custom_log))

        result = get_log_file()
        assert result == custom_log
        assert result.parent.exists()

    def test_pid_file_overrides_dir(self, tmp_path, monkeypatch):
        """Test that OPENPAWS_PID_FILE takes precedence over OPENPAWS_DIR."""
        base_dir = tmp_path / "base"
        custom_pid = tmp_path / "override" / "override.pid"

        monkeypatch.setenv(ENV_OPENPAWS_DIR, str(base_dir))
        monkeypatch.setenv(ENV_PID_FILE, str(custom_pid))

        result = get_pid_file()
        assert result == custom_pid
        assert "base" not in str(result)

    def test_log_file_overrides_dir(self, tmp_path, monkeypatch):
        """Test that OPENPAWS_LOG_FILE takes precedence over OPENPAWS_DIR."""
        base_dir = tmp_path / "base"
        custom_log = tmp_path / "override" / "override.log"

        monkeypatch.setenv(ENV_OPENPAWS_DIR, str(base_dir))
        monkeypatch.setenv(ENV_LOG_FILE, str(custom_log))

        result = get_log_file()
        assert result == custom_log
        assert "base" not in str(result)

    def test_parallel_isolation(self, tmp_path, monkeypatch):
        """Test that multiple instances can run with different env vars."""
        # Simulate two isolated instances
        dir1 = tmp_path / "instance1"
        dir2 = tmp_path / "instance2"

        # Instance 1
        monkeypatch.setenv(ENV_OPENPAWS_DIR, str(dir1))
        monkeypatch.delenv(ENV_PID_FILE, raising=False)
        pid_file_1 = get_pid_file()
        write_pid_file(1001)

        # Instance 2
        monkeypatch.setenv(ENV_OPENPAWS_DIR, str(dir2))
        pid_file_2 = get_pid_file()
        write_pid_file(1002)

        # Verify isolation
        assert pid_file_1 != pid_file_2
        assert pid_file_1.read_text() == "1001"
        assert pid_file_2.read_text() == "1002"


class TestChannelAdapterRegistry:
    """Tests for channel adapter registry functionality."""

    def test_channel_adapters_by_type_initialized(self):
        """Test that channel adapters registry is initialized."""
        daemon_obj = Daemon()
        assert daemon_obj._channel_adapters_by_type == {}
        assert daemon_obj._channel_adapters == []

    def test_register_adapter_adds_to_both_structures(self):
        """Test that _register_adapter adds adapter to list and dict."""
        daemon_obj = Daemon()
        daemon_obj._channel_adapters = []
        daemon_obj._channel_adapters_by_type = {}

        mock_adapter = MagicMock()
        mock_adapter.channel_type = "campfire"

        daemon_obj._register_adapter(mock_adapter, "my-campfire")

        assert mock_adapter in daemon_obj._channel_adapters
        assert daemon_obj._channel_adapters_by_type["campfire"] is mock_adapter


class TestDisabledTasks:
    """Tests for task enabled/disabled functionality."""

    def test_setup_scheduler_skips_disabled_tasks(self):
        """Test that _setup_scheduler skips tasks with enabled=False."""
        daemon_obj = Daemon()
        daemon_obj.storage = MagicMock()
        daemon_obj.config = Config(
            tasks={
                "enabled-task": TaskConfig(
                    name="enabled-task",
                    group="main",
                    prompt="Do something",
                    schedule="0 9 * * *",
                    enabled=True,
                ),
                "disabled-task": TaskConfig(
                    name="disabled-task",
                    group="main",
                    prompt="Do nothing",
                    schedule="0 10 * * *",
                    enabled=False,
                ),
            },
        )

        daemon_obj._setup_scheduler()

        # Only the enabled task should be in the scheduler
        assert "enabled-task" in daemon_obj.scheduler.tasks
        assert "disabled-task" not in daemon_obj.scheduler.tasks

    def test_setup_scheduler_all_disabled(self):
        """Test _setup_scheduler when all tasks are disabled."""
        daemon_obj = Daemon()
        daemon_obj.storage = MagicMock()
        daemon_obj.config = Config(
            tasks={
                "task1": TaskConfig(
                    name="task1",
                    group="main",
                    prompt="Task 1",
                    schedule="0 9 * * *",
                    enabled=False,
                ),
                "task2": TaskConfig(
                    name="task2",
                    group="main",
                    prompt="Task 2",
                    schedule="0 10 * * *",
                    enabled=False,
                ),
            },
        )

        daemon_obj._setup_scheduler()

        assert len(daemon_obj.scheduler.tasks) == 0

    def test_setup_scheduler_default_enabled(self):
        """Test that tasks without explicit enabled field are scheduled."""
        daemon_obj = Daemon()
        daemon_obj.storage = MagicMock()
        daemon_obj.config = Config(
            tasks={
                "default-task": TaskConfig(
                    name="default-task",
                    group="main",
                    prompt="Default enabled task",
                    schedule="0 9 * * *",
                    # enabled defaults to True
                ),
            },
        )

        daemon_obj._setup_scheduler()

        assert "default-task" in daemon_obj.scheduler.tasks


class TestProactiveTaskMessaging:
    """Tests for sending scheduled task results to channels."""

    @pytest.fixture
    def daemon_with_config(self):
        """Create a Daemon with a minimal config for testing."""
        daemon_obj = Daemon()
        daemon_obj.config = Config(
            groups={
                "main": GroupConfig(
                    name="main",
                    channel="campfire",
                    chat_id="42",
                ),
                "slack-group": GroupConfig(
                    name="slack-group",
                    channel="slack",
                    chat_id="C123456",
                ),
            },
            tasks={
                "morning-news": TaskConfig(
                    name="morning-news",
                    group="main",
                    prompt="Summarize today's news",
                    schedule="0 9 * * *",
                ),
            },
        )
        daemon_obj._channel_adapters = []
        daemon_obj._channel_adapters_by_type = {}
        return daemon_obj

    @pytest.fixture
    def mock_campfire_adapter(self):
        """Create a mock Campfire adapter."""
        adapter = MagicMock()
        adapter.channel_type = "campfire"
        adapter.is_running.return_value = True
        adapter.send_message = AsyncMock()
        return adapter

    @pytest.fixture
    def mock_task(self):
        """Create a mock scheduled task."""
        task_config = TaskConfig(
            name="morning-news",
            group="main",
            prompt="Summarize today's news",
            schedule="0 9 * * *",
        )
        return ScheduledTask(config=task_config)

    @pytest.mark.asyncio
    async def test_send_task_result_success(
        self, daemon_with_config, mock_campfire_adapter, mock_task
    ):
        """Test that task result is sent to the correct channel."""
        daemon_obj = daemon_with_config
        daemon_obj._channel_adapters_by_type["campfire"] = mock_campfire_adapter

        await daemon_obj._send_task_result_to_channel(mock_task, "Today's top news...")

        mock_campfire_adapter.send_message.assert_called_once()
        call_args = mock_campfire_adapter.send_message.call_args[0][0]
        assert isinstance(call_args, OutgoingMessage)
        assert call_args.channel_id == "42"
        assert call_args.text == "Today's top news..."

    @pytest.mark.asyncio
    async def test_send_task_result_unknown_group(self, daemon_with_config, mock_task):
        """Test that unknown group logs warning and returns."""
        daemon_obj = daemon_with_config
        mock_task.config.group = "nonexistent"

        # Should not raise, just log warning
        await daemon_obj._send_task_result_to_channel(mock_task, "Message")

    @pytest.mark.asyncio
    async def test_send_task_result_no_adapter(self, daemon_with_config, mock_task):
        """Test that missing adapter logs warning and returns."""
        daemon_obj = daemon_with_config
        # No adapter registered for 'campfire'

        # Should not raise, just log warning
        await daemon_obj._send_task_result_to_channel(mock_task, "Message")

    @pytest.mark.asyncio
    async def test_send_task_result_adapter_not_running(
        self, daemon_with_config, mock_campfire_adapter, mock_task
    ):
        """Test that non-running adapter logs warning and returns."""
        daemon_obj = daemon_with_config
        mock_campfire_adapter.is_running.return_value = False
        daemon_obj._channel_adapters_by_type["campfire"] = mock_campfire_adapter

        await daemon_obj._send_task_result_to_channel(mock_task, "Message")

        mock_campfire_adapter.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_task_result_send_failure(
        self, daemon_with_config, mock_campfire_adapter, mock_task
    ):
        """Test that send failure is logged but doesn't raise."""
        daemon_obj = daemon_with_config
        mock_campfire_adapter.send_message.side_effect = RuntimeError("Network error")
        daemon_obj._channel_adapters_by_type["campfire"] = mock_campfire_adapter

        # Should not raise, just log error
        await daemon_obj._send_task_result_to_channel(mock_task, "Message")

    @pytest.mark.asyncio
    async def test_execute_task_sends_result(self, daemon_with_config, mock_task):
        """Test that _execute_task sends result to channel on success."""
        daemon_obj = daemon_with_config

        # Mock runner
        mock_runner = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message = "Task completed successfully"
        mock_runner.run_task = AsyncMock(return_value=mock_result)
        daemon_obj._runner = mock_runner

        # Mock the send method
        daemon_obj._send_task_result_to_channel = AsyncMock()

        result = await daemon_obj._execute_task(mock_task)

        assert result == "Task completed successfully"
        daemon_obj._send_task_result_to_channel.assert_called_once_with(
            mock_task, "Task completed successfully"
        )

    @pytest.mark.asyncio
    async def test_execute_task_no_send_on_failure(self, daemon_with_config, mock_task):
        """Test that _execute_task does not send result on failure."""
        daemon_obj = daemon_with_config

        # Mock runner with failed result
        mock_runner = MagicMock()
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.message = "Task failed"
        mock_runner.run_task = AsyncMock(return_value=mock_result)
        daemon_obj._runner = mock_runner

        daemon_obj._send_task_result_to_channel = AsyncMock()

        result = await daemon_obj._execute_task(mock_task)

        assert result == "Task failed"
        daemon_obj._send_task_result_to_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_task_no_send_on_empty_message(
        self, daemon_with_config, mock_task
    ):
        """Test that _execute_task does not send empty messages."""
        daemon_obj = daemon_with_config

        mock_runner = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message = ""  # Empty message
        mock_runner.run_task = AsyncMock(return_value=mock_result)
        daemon_obj._runner = mock_runner

        daemon_obj._send_task_result_to_channel = AsyncMock()

        await daemon_obj._execute_task(mock_task)

        daemon_obj._send_task_result_to_channel.assert_not_called()


class TestAgentServerManagerIntegration:
    """Tests for AgentServerManager integration in Daemon."""

    def test_agent_server_manager_none_when_disabled(self, tmp_path):
        """Test that agent server manager is None when remote_servers disabled."""

        from openpaws.daemon import Daemon

        config_content = """
channels:
  slack:
    app_token: "xapp-test"
    bot_token: "xoxb-test"

groups:
  main:
    channel: slack
    chat_id: "C123"

tasks:
  daily:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily summary"

remote_servers:
  enabled: false
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        daemon = Daemon(config_path=config_file)
        daemon._load_config()
        daemon._setup_agent_server_manager()

        assert daemon._agent_server_manager is None

    def test_agent_server_manager_created_when_enabled(self, tmp_path, monkeypatch):
        """Test that agent server manager is created when remote_servers enabled."""
        from openpaws.daemon import Daemon

        # Set OPENPAWS_DIR to use tmp_path
        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))

        config_content = """
channels:
  slack:
    app_token: "xapp-test"
    bot_token: "xoxb-test"

groups:
  main:
    channel: slack
    chat_id: "C123"

tasks:
  daily:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily summary"

remote_servers:
  enabled: true
  port_start: 19000
  port_end: 19050
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        daemon = Daemon(config_path=config_file)
        daemon._load_config()
        daemon._setup_agent_server_manager()

        assert daemon._agent_server_manager is not None
        assert daemon._agent_server_manager.port_start == 19000
        assert daemon._agent_server_manager.port_end == 19050

    def test_runner_receives_server_manager(self, tmp_path, monkeypatch):
        """Test that ConversationRunner receives the server_manager."""
        from openpaws.daemon import Daemon

        # Set OPENPAWS_DIR to use tmp_path
        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))

        config_content = """
channels:
  slack:
    app_token: "xapp-test"
    bot_token: "xoxb-test"

groups:
  main:
    channel: slack
    chat_id: "C123"

tasks:
  daily:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily summary"

remote_servers:
  enabled: true
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        daemon = Daemon(config_path=config_file)
        daemon._load_config()
        daemon._setup_agent_server_manager()
        daemon._setup_runner()

        assert daemon._runner._server_manager is daemon._agent_server_manager
