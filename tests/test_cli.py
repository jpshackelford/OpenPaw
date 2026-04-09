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


class TestCampfireSetupHelpers:
    """Tests for Campfire setup wizard helper functions.

    Note: These tests now import from openpaws.channels.campfire_setup
    and openpaws.terminal modules after the refactoring.
    """

    def test_parse_campfire_curl_valid(self):
        """Test parsing a valid Campfire curl command."""
        from openpaws.channels.campfire_setup import parse_campfire_curl

        curl_cmd = (
            "curl -d 'Hello!' http://campfire.localhost/rooms/1/2-rk2SGfi9lZW0/messages"
        )
        result = parse_campfire_curl(curl_cmd)

        assert result is not None
        base_url, room_id, bot_key = result
        assert base_url == "http://campfire.localhost"
        assert room_id == "1"
        assert bot_key == "2-rk2SGfi9lZW0"

    def test_parse_campfire_curl_https(self):
        """Test parsing curl command with https."""
        from openpaws.channels.campfire_setup import parse_campfire_curl

        curl_cmd = (
            "curl -d 'test' https://chat.example.com/rooms/42/abc-xyz123/messages"
        )
        result = parse_campfire_curl(curl_cmd)

        assert result is not None
        assert result[0] == "https://chat.example.com"
        assert result[1] == "42"
        assert result[2] == "abc-xyz123"

    def test_parse_campfire_curl_invalid(self):
        """Test parsing an invalid/non-matching string."""
        from openpaws.channels.campfire_setup import parse_campfire_curl

        assert parse_campfire_curl("not a curl command") is None
        assert parse_campfire_curl("curl http://example.com") is None
        assert parse_campfire_curl("") is None

    def test_parse_campfire_curl_just_bot_key(self):
        """Test that a plain bot key doesn't parse as curl."""
        from openpaws.channels.campfire_setup import parse_campfire_curl

        # A plain bot key should not match
        assert parse_campfire_curl("2-rk2SGfi9lZW0") is None

    def test_campfire_normalize_url_with_trailing_slash(self):
        """Test URL normalization removes trailing slash."""
        from openpaws.channels.campfire_setup import normalize_url

        result = normalize_url("http://campfire.localhost/")
        assert result == "http://campfire.localhost"

    def test_campfire_normalize_url_adds_http(self):
        """Test URL normalization adds http:// if missing."""
        from openpaws.channels.campfire_setup import normalize_url

        result = normalize_url("campfire.localhost")
        assert result == "http://campfire.localhost"

    def test_campfire_normalize_url_preserves_https(self):
        """Test URL normalization preserves https."""
        from openpaws.channels.campfire_setup import normalize_url

        result = normalize_url("https://chat.example.com")
        assert result == "https://chat.example.com"

    def test_campfire_http_error_to_result_302(self):
        """Test HTTP 302 error maps to invalid_key."""
        from unittest.mock import MagicMock

        from openpaws.channels.campfire_setup import http_error_to_result

        error = MagicMock()
        error.code = 302
        success, msg = http_error_to_result(error)
        assert success is False
        assert msg == "invalid_key"

    def test_campfire_http_error_to_result_500(self):
        """Test HTTP 500 error maps to invalid_room."""
        from unittest.mock import MagicMock

        from openpaws.channels.campfire_setup import http_error_to_result

        error = MagicMock()
        error.code = 500
        success, msg = http_error_to_result(error)
        assert success is False
        assert msg == "invalid_room"

    def test_campfire_http_error_to_result_other(self):
        """Test other HTTP errors return code in message."""
        from unittest.mock import MagicMock

        from openpaws.channels.campfire_setup import http_error_to_result

        error = MagicMock()
        error.code = 404
        success, msg = http_error_to_result(error)
        assert success is False
        assert msg == "http_404"

    def test_build_campfire_request(self):
        """Test building a Campfire test request."""
        from openpaws.channels.campfire_setup import build_test_request

        req = build_test_request("http://campfire.localhost", "1", "2-abc123")

        assert req.full_url == "http://campfire.localhost/rooms/1/2-abc123/messages"
        assert req.data == "🐾 OpenPaws connected successfully!".encode()
        assert req.get_header("Content-type") == "text/plain; charset=utf-8"

    def test_get_config_dir_default(self, monkeypatch, tmp_path):
        """Test get_config_dir returns default path."""
        from openpaws.channels.campfire_setup import get_config_dir

        # Remove OPENPAWS_DIR if set
        monkeypatch.delenv("OPENPAWS_DIR", raising=False)
        # Mock home directory
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        result = get_config_dir()
        assert result == tmp_path / ".openpaws"

    def test_get_config_dir_from_env(self, monkeypatch, tmp_path):
        """Test get_config_dir uses OPENPAWS_DIR env var."""
        from openpaws.channels.campfire_setup import get_config_dir

        custom_dir = tmp_path / "custom_openpaws"
        monkeypatch.setenv("OPENPAWS_DIR", str(custom_dir))

        result = get_config_dir()
        assert result == custom_dir

    def test_get_config_file(self, monkeypatch, tmp_path):
        """Test get_config_file returns correct path."""
        from openpaws.channels.campfire_setup import get_config_file

        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))

        result = get_config_file()
        assert result == tmp_path / "config.yaml"

    def test_load_config_yaml_empty(self, monkeypatch, tmp_path):
        """Test loading config when file doesn't exist."""
        from openpaws.channels.campfire_setup import load_config_yaml

        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))

        result = load_config_yaml()
        assert result == {}

    def test_load_config_yaml_existing(self, monkeypatch, tmp_path):
        """Test loading existing config file."""
        from openpaws.channels.campfire_setup import load_config_yaml

        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))
        config_file = tmp_path / "config.yaml"
        config_file.write_text("channels:\n  test: value\n")

        result = load_config_yaml()
        assert result == {"channels": {"test": "value"}}

    def test_save_config_yaml(self, monkeypatch, tmp_path):
        """Test saving config to YAML file."""
        from openpaws.channels.campfire_setup import save_config_yaml

        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))

        config = {"channels": {"campfire": {"url": "http://test"}}}
        save_config_yaml(config)

        config_file = tmp_path / "config.yaml"
        assert config_file.exists()
        content = config_file.read_text()
        assert "channels:" in content
        assert "campfire:" in content

    def test_parse_yes_no_yes(self):
        """Test parsing 'y' as True."""
        from openpaws.terminal import _parse_yes_no

        assert _parse_yes_no("y", default=False) is True
        assert _parse_yes_no("Y", default=False) is True

    def test_parse_yes_no_no(self):
        """Test parsing 'n' as False."""
        from openpaws.terminal import _parse_yes_no

        assert _parse_yes_no("n", default=True) is False
        assert _parse_yes_no("N", default=True) is False

    def test_parse_yes_no_default_cr(self):
        """Test carriage return uses default."""
        from openpaws.terminal import _parse_yes_no

        assert _parse_yes_no("\r", default=True) is True
        assert _parse_yes_no("\r", default=False) is False

    def test_parse_yes_no_default_lf(self):
        """Test newline uses default."""
        from openpaws.terminal import _parse_yes_no

        assert _parse_yes_no("\n", default=True) is True
        assert _parse_yes_no("\n", default=False) is False

    def test_parse_yes_no_default_empty(self):
        """Test empty string uses default."""
        from openpaws.terminal import _parse_yes_no

        assert _parse_yes_no("", default=True) is True
        assert _parse_yes_no("", default=False) is False

    def test_parse_yes_no_invalid_uses_default(self):
        """Test invalid characters use default."""
        from openpaws.terminal import _parse_yes_no

        assert _parse_yes_no("x", default=True) is True
        assert _parse_yes_no("x", default=False) is False

    def test_handle_prompt_char_enter(self):
        """Test handling enter key returns True (done)."""
        from openpaws.terminal import _handle_prompt_char

        result = []
        assert _handle_prompt_char("\r", result, echo=False) is True
        assert _handle_prompt_char("\n", result, echo=False) is True

    def test_handle_prompt_char_ctrl_c(self):
        """Test Ctrl+C raises KeyboardInterrupt."""
        from openpaws.terminal import _handle_prompt_char

        result = []
        with pytest.raises(KeyboardInterrupt):
            _handle_prompt_char("\x03", result, echo=False)

    def test_handle_prompt_char_printable(self):
        """Test printable characters are added to result."""
        from openpaws.terminal import _handle_prompt_char

        result = []
        assert _handle_prompt_char("a", result, echo=False) is False
        assert result == ["a"]
        assert _handle_prompt_char("b", result, echo=False) is False
        assert result == ["a", "b"]

    def test_handle_prompt_char_backspace(self):
        """Test backspace removes last character."""
        from openpaws.terminal import _handle_prompt_char

        result = ["a", "b", "c"]
        assert _handle_prompt_char("\x7f", result, echo=False) is False
        assert result == ["a", "b"]

    def test_handle_prompt_char_backspace_empty(self):
        """Test backspace on empty list does nothing."""
        from openpaws.terminal import _handle_prompt_char

        # With the fixed implementation, backspace on empty list is a no-op
        result = []
        assert _handle_prompt_char("\x7f", result, echo=False) is False
        assert result == []
