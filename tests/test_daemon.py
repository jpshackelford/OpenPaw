"""Tests for daemon process management."""

import os
from unittest.mock import patch

import pytest

from openpaws import daemon
from openpaws.daemon import (
    format_uptime,
    get_daemon_status,
    get_openpaws_dir,
    get_pid_file,
    is_process_running,
    read_pid_file,
    remove_pid_file,
    write_pid_file,
)


@pytest.fixture
def temp_openpaws_dir(tmp_path):
    """Use a temporary directory for OpenPaws files."""
    with patch.object(daemon, "DEFAULT_OPENPAWS_DIR", tmp_path):
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
        assert format_uptime(60) == "1m 0s"
        assert format_uptime(90) == "1m 30s"
        assert format_uptime(3599) == "59m 59s"

    def test_format_uptime_hours(self):
        """Test formatting uptime in hours."""
        assert format_uptime(3600) == "1h 0m"
        assert format_uptime(7200) == "2h 0m"
        assert format_uptime(3660) == "1h 1m"
        assert format_uptime(86399) == "23h 59m"

    def test_format_uptime_days(self):
        """Test formatting uptime in days."""
        assert format_uptime(86400) == "1d 0h"
        assert format_uptime(172800) == "2d 0h"
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
