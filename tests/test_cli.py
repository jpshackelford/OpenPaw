"""Tests for CLI commands."""

import pytest
from click.testing import CliRunner

from openpaws.cli import main
from openpaws.storage import Storage, TaskState


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Set up an isolated environment for CLI tests."""
    monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def config_file(isolated_env):
    """Create a minimal config file in the OPENPAWS_DIR."""
    # Config file is expected at ~/.openpaws/config.yaml (or OPENPAWS_DIR/config.yaml)
    config_path = isolated_env / "config.yaml"
    config_path.write_text("""
channels:
  telegram:
    bot_token: test-token

groups:
  main:
    channel: telegram
    chat_id: "123456"
    trigger: "@paw"

tasks:
  morning-news:
    schedule: "0 8 * * *"
    group: main
    prompt: "Summarize the news"
  
  hourly-check:
    interval: 3600
    group: main
    prompt: "Check status"
    
agent:
  model: test-model
""")
    return config_path


@pytest.fixture
def storage_with_env(isolated_env):
    """Create a Storage instance using the isolated env."""
    return Storage()


class TestTasksList:
    """Tests for tasks list command."""

    def test_tasks_list_no_tasks(self, runner, isolated_env):
        """Test tasks list with no config and no stored tasks."""
        result = runner.invoke(main, ["tasks", "list"])
        assert result.exit_code == 0
        assert "No scheduled tasks found" in result.output

    def test_tasks_list_from_config(self, runner, config_file, isolated_env):
        """Test tasks list shows tasks from config."""
        result = runner.invoke(main, ["tasks", "list"])
        assert result.exit_code == 0
        assert "Scheduled Tasks" in result.output
        assert "morning-news" in result.output
        assert "hourly-check" in result.output
        assert "0 8 * * *" in result.output

    def test_tasks_list_shows_stored_state(self, runner, config_file, storage_with_env):
        """Test tasks list shows stored task state."""
        storage_with_env.save_task(
            TaskState(
                name="morning-news",
                schedule="0 8 * * *",
                status="paused",
            )
        )

        result = runner.invoke(main, ["tasks", "list"])
        assert result.exit_code == 0
        assert "paused" in result.output


class TestTasksPause:
    """Tests for tasks pause command."""

    def test_pause_nonexistent_task(self, runner, isolated_env):
        """Test pausing a task that doesn't exist."""
        result = runner.invoke(main, ["tasks", "pause", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_pause_task(self, runner, config_file, storage_with_env):
        """Test pausing a task."""
        result = runner.invoke(main, ["tasks", "pause", "morning-news"])
        assert result.exit_code == 0
        assert "paused" in result.output

        task = storage_with_env.load_task("morning-news")
        assert task is not None
        assert task.status == "paused"

    def test_pause_already_paused(self, runner, config_file, storage_with_env):
        """Test pausing a task that's already paused."""
        storage_with_env.save_task(
            TaskState(
                name="morning-news",
                schedule="0 8 * * *",
                status="paused",
            )
        )

        result = runner.invoke(main, ["tasks", "pause", "morning-news"])
        assert result.exit_code == 0
        assert "already paused" in result.output


class TestTasksResume:
    """Tests for tasks resume command."""

    def test_resume_nonexistent_task(self, runner, isolated_env):
        """Test resuming a task that doesn't exist."""
        result = runner.invoke(main, ["tasks", "resume", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_resume_not_paused(self, runner, config_file, storage_with_env):
        """Test resuming a task that's not paused."""
        result = runner.invoke(main, ["tasks", "resume", "morning-news"])
        assert result.exit_code == 0
        assert "not paused" in result.output

    def test_resume_paused_task(self, runner, config_file, storage_with_env):
        """Test resuming a paused task."""
        storage_with_env.save_task(
            TaskState(
                name="morning-news",
                schedule="0 8 * * *",
                status="paused",
            )
        )

        result = runner.invoke(main, ["tasks", "resume", "morning-news"])
        assert result.exit_code == 0
        assert "resumed" in result.output

        task = storage_with_env.load_task("morning-news")
        assert task is not None
        assert task.status == "active"
        assert task.next_run is not None


class TestTasksRun:
    """Tests for tasks run command."""

    def test_run_nonexistent_task(self, runner, isolated_env):
        """Test running a task that doesn't exist."""
        result = runner.invoke(main, ["tasks", "run", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output


class TestLogs:
    """Tests for logs command."""

    def test_logs_no_file(self, runner, isolated_env):
        """Test logs when log file doesn't exist."""
        result = runner.invoke(main, ["logs"])
        assert result.exit_code == 0
        assert "not found" in result.output

    def test_logs_empty_file(self, runner, isolated_env):
        """Test logs with empty log file."""
        log_dir = isolated_env / "logs"
        log_dir.mkdir()
        log_file = log_dir / "openpaws.log"
        log_file.write_text("")

        result = runner.invoke(main, ["logs"])
        assert result.exit_code == 0
        assert "empty" in result.output

    def test_logs_show_content(self, runner, isolated_env):
        """Test logs shows log content."""
        log_dir = isolated_env / "logs"
        log_dir.mkdir()
        log_file = log_dir / "openpaws.log"
        log_file.write_text("2024-03-15 10:00:00 - INFO - Test log message\n")

        result = runner.invoke(main, ["logs"])
        assert result.exit_code == 0
        assert "Test log message" in result.output

    def test_logs_filter_by_pattern(self, runner, isolated_env):
        """Test logs filtering."""
        log_dir = isolated_env / "logs"
        log_dir.mkdir()
        log_file = log_dir / "openpaws.log"
        log_file.write_text(
            "2024-03-15 10:00:00 - INFO - Task morning-news started\n"
            "2024-03-15 10:00:01 - INFO - Task hourly-check completed\n"
        )

        result = runner.invoke(main, ["logs", "--task", "morning"])
        assert result.exit_code == 0
        assert "morning-news" in result.output
        assert "hourly-check" not in result.output

    def test_logs_lines_option(self, runner, isolated_env):
        """Test logs with custom line count."""
        log_dir = isolated_env / "logs"
        log_dir.mkdir()
        log_file = log_dir / "openpaws.log"
        lines = [f"Line {i}\n" for i in range(100)]
        log_file.write_text("".join(lines))

        result = runner.invoke(main, ["logs", "--lines", "10"])
        assert result.exit_code == 0
        assert "Line 99" in result.output
        assert "Line 89" not in result.output


class TestTasksAdd:
    """Tests for tasks add command."""

    def test_add_requires_schedule(self, runner, isolated_env):
        """Test that add requires a schedule option."""
        result = runner.invoke(
            main,
            [
                "tasks",
                "add",
                "-g",
                "main",
                "-p",
                "Test prompt",
                "test-task",
            ],
        )
        assert result.exit_code == 1
        assert "Must specify one of" in result.output

    def test_add_multiple_schedules(self, runner, isolated_env):
        """Test that add rejects multiple schedule types."""
        result = runner.invoke(
            main,
            [
                "tasks",
                "add",
                "--schedule",
                "0 8 * * *",
                "--every",
                "1h",
                "-g",
                "main",
                "-p",
                "Test prompt",
                "test-task",
            ],
        )
        assert result.exit_code == 1
        assert "Specify only one" in result.output

    def test_add_with_schedule(self, runner, isolated_env):
        """Test add with cron schedule (stub behavior)."""
        result = runner.invoke(
            main,
            [
                "tasks",
                "add",
                "--schedule",
                "0 8 * * *",
                "-g",
                "main",
                "-p",
                "Test prompt",
                "test-task",
            ],
        )
        assert result.exit_code == 0
        assert "Adding task" in result.output
        # Currently still shows not implemented
        assert "not yet implemented" in result.output.lower()
