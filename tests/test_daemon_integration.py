"""Integration tests for daemon process management.

These tests actually start and stop daemon processes, using environment
variables to isolate each test instance.

Subprocess Coverage
-------------------
To collect coverage from daemon subprocesses, we use coverage.py's
subprocess support. The key mechanism:

1. Set COVERAGE_PROCESS_START to point to .coveragerc
2. Ensure coverage.process_startup() is called in subprocesses
3. Use 'coverage combine' to merge data from all processes

Run with: coverage run -m pytest && coverage combine && coverage report
"""

import os
import subprocess
import sys
import time

import pytest

from openpaws.daemon import (
    ENV_LOG_FILE,
    ENV_OPENPAWS_DIR,
    ENV_PID_FILE,
)


@pytest.fixture
def isolated_daemon_env(tmp_path):
    """Create an isolated environment for daemon testing.

    Returns a dict of environment variables that can be passed to subprocess.
    Each test gets its own directory to avoid conflicts during parallel execution.

    Also configures coverage collection in subprocesses if COVERAGE_PROCESS_START
    is set in the parent environment.
    """
    env = os.environ.copy()
    env[ENV_OPENPAWS_DIR] = str(tmp_path)
    # Clear any explicit overrides from parent environment
    env.pop(ENV_PID_FILE, None)
    env.pop(ENV_LOG_FILE, None)

    # Propagate coverage settings to subprocesses
    # If running under coverage, COVERAGE_PROCESS_START will be set
    # and subprocesses will auto-start coverage collection
    pyproject = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
    if os.path.exists(pyproject):
        env["COVERAGE_PROCESS_START"] = os.path.abspath(pyproject)

    return env


@pytest.fixture
def config_file(tmp_path):
    """Create a minimal config file for testing."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("""
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"
    trigger: "@paw"

tasks:
  test-task:
    schedule: "0 9 * * *"
    group: main
    prompt: "Test prompt"
""")
    return config_path


def run_openpaws(
    args: list[str], env: dict, timeout: int = 30
) -> subprocess.CompletedProcess:
    """Run openpaws CLI command with given environment."""
    cmd = [sys.executable, "-m", "openpaws"] + args
    return subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestDaemonLifecycle:
    """Integration tests for daemon start/stop/status lifecycle."""

    def test_status_when_not_running(self, isolated_daemon_env):
        """Test status command when daemon is not running."""
        result = run_openpaws(["status"], isolated_daemon_env)

        assert result.returncode == 0
        assert "Stopped" in result.stdout

    def test_start_and_status(self, isolated_daemon_env, tmp_path):
        """Test starting daemon and checking status."""
        # Start daemon
        result = run_openpaws(["start"], isolated_daemon_env)
        assert result.returncode == 0
        assert "Starting" in result.stdout

        # Give it a moment to fully start
        time.sleep(0.5)

        try:
            # Check status
            result = run_openpaws(["status"], isolated_daemon_env)
            assert result.returncode == 0
            assert "Running" in result.stdout
            assert "PID" in result.stdout

            # Verify PID file exists
            pid_file = tmp_path / "openpaws.pid"
            assert pid_file.exists()
            pid = int(pid_file.read_text().strip())
            assert pid > 0
        finally:
            # Cleanup: stop the daemon
            run_openpaws(["stop"], isolated_daemon_env)

    def test_start_stop_cycle(self, isolated_daemon_env, tmp_path):
        """Test full start/stop cycle."""
        # Start
        result = run_openpaws(["start"], isolated_daemon_env)
        assert result.returncode == 0
        time.sleep(0.5)

        # Verify running
        pid_file = tmp_path / "openpaws.pid"
        assert pid_file.exists()
        assert int(pid_file.read_text().strip()) > 0

        # Stop
        result = run_openpaws(["stop"], isolated_daemon_env)
        assert result.returncode == 0
        assert "stopped" in result.stdout.lower()

        # Verify stopped
        result = run_openpaws(["status"], isolated_daemon_env)
        assert "Stopped" in result.stdout

    def test_start_with_config(self, isolated_daemon_env, config_file, tmp_path):
        """Test starting daemon with a config file."""
        result = run_openpaws(
            ["start", "--config", str(config_file)],
            isolated_daemon_env,
        )
        assert result.returncode == 0
        time.sleep(0.5)

        try:
            # Check that it's running
            result = run_openpaws(["status"], isolated_daemon_env)
            assert "Running" in result.stdout

            # Check log file was created and has content
            log_file = tmp_path / "logs" / "openpaws.log"
            assert log_file.exists()
            log_content = log_file.read_text()
            assert "Configuration loaded" in log_content
            assert "test-task" in log_content
        finally:
            run_openpaws(["stop"], isolated_daemon_env)

    def test_start_already_running(self, isolated_daemon_env):
        """Test that starting when already running fails gracefully."""
        # Start first instance
        result = run_openpaws(["start"], isolated_daemon_env)
        assert result.returncode == 0
        time.sleep(0.5)

        try:
            # Try to start again
            result = run_openpaws(["start"], isolated_daemon_env)
            assert result.returncode == 1
            assert "already running" in result.stdout.lower()
        finally:
            run_openpaws(["stop"], isolated_daemon_env)

    def test_stop_when_not_running(self, isolated_daemon_env):
        """Test stopping when daemon is not running."""
        result = run_openpaws(["stop"], isolated_daemon_env)
        assert result.returncode == 0
        assert "not running" in result.stdout.lower()


class TestParallelDaemons:
    """Test that multiple daemon instances can run in parallel."""

    def test_two_daemons_parallel(self, tmp_path):
        """Test running two daemon instances with different directories."""
        dir1 = tmp_path / "daemon1"
        dir2 = tmp_path / "daemon2"
        dir1.mkdir()
        dir2.mkdir()

        # Create isolated environments for each daemon
        env1 = os.environ.copy()
        env1[ENV_OPENPAWS_DIR] = str(dir1)
        env1.pop(ENV_PID_FILE, None)
        env1.pop(ENV_LOG_FILE, None)

        env2 = os.environ.copy()
        env2[ENV_OPENPAWS_DIR] = str(dir2)
        env2.pop(ENV_PID_FILE, None)
        env2.pop(ENV_LOG_FILE, None)

        # Propagate coverage settings
        pyproject = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
        if os.path.exists(pyproject):
            abs_pyproject = os.path.abspath(pyproject)
            env1["COVERAGE_PROCESS_START"] = abs_pyproject
            env2["COVERAGE_PROCESS_START"] = abs_pyproject

        try:
            # Start both daemons
            result1 = run_openpaws(["start"], env1)
            assert result1.returncode == 0

            result2 = run_openpaws(["start"], env2)
            assert result2.returncode == 0

            time.sleep(0.5)

            # Both should be running with different PIDs
            pid1 = int((dir1 / "openpaws.pid").read_text().strip())
            pid2 = int((dir2 / "openpaws.pid").read_text().strip())
            assert pid1 != pid2

            # Both should report running status
            status1 = run_openpaws(["status"], env1)
            status2 = run_openpaws(["status"], env2)
            assert "Running" in status1.stdout
            assert "Running" in status2.stdout

        finally:
            # Stop both
            run_openpaws(["stop"], env1)
            run_openpaws(["stop"], env2)


class TestLogging:
    """Integration tests for daemon logging."""

    def test_log_file_created(self, isolated_daemon_env, tmp_path):
        """Test that log file is created when daemon starts."""
        result = run_openpaws(["start"], isolated_daemon_env)
        assert result.returncode == 0
        time.sleep(0.5)

        try:
            log_file = tmp_path / "logs" / "openpaws.log"
            assert log_file.exists()
            assert log_file.stat().st_size > 0
        finally:
            run_openpaws(["stop"], isolated_daemon_env)

    def test_log_contains_startup_info(self, isolated_daemon_env, tmp_path):
        """Test that log contains expected startup information."""
        result = run_openpaws(["start"], isolated_daemon_env)
        assert result.returncode == 0
        time.sleep(0.5)

        try:
            log_file = tmp_path / "logs" / "openpaws.log"
            log_content = log_file.read_text()

            assert "PID file written" in log_content
            assert "daemon started" in log_content.lower()
        finally:
            run_openpaws(["stop"], isolated_daemon_env)

    def test_log_contains_shutdown_info(self, isolated_daemon_env, tmp_path):
        """Test that log contains shutdown information."""
        run_openpaws(["start"], isolated_daemon_env)
        time.sleep(0.5)
        run_openpaws(["stop"], isolated_daemon_env)
        time.sleep(0.5)

        log_file = tmp_path / "logs" / "openpaws.log"
        log_content = log_file.read_text()

        assert "daemon stopped" in log_content.lower()
