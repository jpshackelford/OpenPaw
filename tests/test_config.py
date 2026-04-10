"""Tests for configuration loading."""

import os
import tempfile

import pytest

from openpaws.config import (
    QueueConfig,
    _parse_interval,
    _validate_task_schedule,
    expand_env_vars,
    load_config,
)


def test_expand_env_vars():
    """Test environment variable expansion."""
    os.environ["TEST_VAR"] = "hello"

    assert expand_env_vars("${TEST_VAR}") == "hello"
    assert expand_env_vars("prefix_${TEST_VAR}_suffix") == "prefix_hello_suffix"
    assert expand_env_vars("no vars here") == "no vars here"
    assert expand_env_vars("${NONEXISTENT}") == "${NONEXISTENT}"


def test_load_config():
    """Test loading a config file."""
    config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"
    trigger: "@paw"
    admin: true

tasks:
  test-task:
    schedule: "0 9 * * *"
    group: main
    prompt: "Say hello"

agent:
  model: anthropic/claude-sonnet-4-20250514
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        f.flush()

        config = load_config(f.name)

        assert "telegram" in config.channels
        assert config.channels["telegram"].bot_token == "test-token"

        assert "main" in config.groups
        assert config.groups["main"].admin is True

        assert "test-task" in config.tasks
        assert config.tasks["test-task"].schedule == "0 9 * * *"

        assert config.agent.model == "anthropic/claude-sonnet-4-20250514"

    os.unlink(f.name)


def test_load_config_missing_file():
    """Test error when config file doesn't exist."""
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path.yaml")


class TestParseInterval:
    """Tests for _parse_interval function."""

    def test_parse_integer(self):
        """Test parsing plain integer (seconds)."""
        assert _parse_interval(3600) == 3600
        assert _parse_interval(60) == 60
        assert _parse_interval(1) == 1

    def test_parse_seconds_string(self):
        """Test parsing string with 's' suffix."""
        assert _parse_interval("60s") == 60
        assert _parse_interval("3600s") == 3600

    def test_parse_minutes_string(self):
        """Test parsing string with 'm' suffix."""
        assert _parse_interval("1m") == 60
        assert _parse_interval("30m") == 1800
        assert _parse_interval("60m") == 3600

    def test_parse_hours_string(self):
        """Test parsing string with 'h' suffix."""
        assert _parse_interval("1h") == 3600
        assert _parse_interval("2h") == 7200
        assert _parse_interval("24h") == 86400

    def test_parse_plain_number_string(self):
        """Test parsing string without suffix (assumes seconds)."""
        assert _parse_interval("3600") == 3600
        assert _parse_interval("60") == 60

    def test_parse_with_whitespace(self):
        """Test parsing handles whitespace."""
        assert _parse_interval(" 1h ") == 3600
        assert _parse_interval("30m ") == 1800


class TestValidateTaskSchedule:
    """Tests for _validate_task_schedule function."""

    def test_valid_cron_schedule(self):
        """Test that cron schedule is valid."""
        cfg = {"schedule": "0 9 * * *", "group": "main", "prompt": "hi"}
        _validate_task_schedule("test", cfg)  # Should not raise

    def test_valid_interval(self):
        """Test that interval is valid."""
        cfg = {"interval": 3600, "group": "main", "prompt": "hi"}
        _validate_task_schedule("test", cfg)  # Should not raise

    def test_valid_once(self):
        """Test that once timestamp is valid."""
        cfg = {"once": "2024-03-15 09:00", "group": "main", "prompt": "hi"}
        _validate_task_schedule("test", cfg)  # Should not raise

    def test_no_schedule_type(self):
        """Test error when no schedule type is provided."""
        with pytest.raises(ValueError) as exc_info:
            _validate_task_schedule("test", {"group": "main", "prompt": "hi"})
        assert "must have one of" in str(exc_info.value)

    def test_multiple_schedule_types(self):
        """Test error when multiple schedule types are provided."""
        with pytest.raises(ValueError) as exc_info:
            _validate_task_schedule(
                "test",
                {
                    "schedule": "0 9 * * *",
                    "interval": 3600,
                    "group": "main",
                    "prompt": "hi",
                },
            )
        assert "multiple schedule types" in str(exc_info.value)


class TestLoadConfigIntervalAndOnce:
    """Tests for loading config with interval and once tasks."""

    def test_load_interval_task(self):
        """Test loading config with interval-based task."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  heartbeat:
    interval: 3600
    group: main
    prompt: "Check system health"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)

            assert "heartbeat" in config.tasks
            assert config.tasks["heartbeat"].interval == 3600
            assert config.tasks["heartbeat"].schedule is None
            assert config.tasks["heartbeat"].once is None

        os.unlink(f.name)

    def test_load_interval_task_with_unit(self):
        """Test loading config with interval using time unit."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  heartbeat:
    interval: "1h"
    group: main
    prompt: "Check system health"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)

            assert config.tasks["heartbeat"].interval == 3600

        os.unlink(f.name)

    def test_load_once_task(self):
        """Test loading config with one-time task."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  reminder:
    once: "2024-03-15T09:00:00"
    group: main
    prompt: "Remind me about the meeting"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)

            assert "reminder" in config.tasks
            assert config.tasks["reminder"].once == "2024-03-15T09:00:00"
            assert config.tasks["reminder"].schedule is None
            assert config.tasks["reminder"].interval is None

        os.unlink(f.name)

    def test_load_mixed_task_types(self):
        """Test loading config with different task types."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  daily:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily summary"

  heartbeat:
    interval: "30m"
    group: main
    prompt: "Check system health"

  reminder:
    once: "2024-03-15 09:00"
    group: main
    prompt: "Remind me about the meeting"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)

            assert config.tasks["daily"].schedule == "0 9 * * *"
            assert config.tasks["heartbeat"].interval == 1800  # 30 minutes
            assert config.tasks["reminder"].once == "2024-03-15 09:00"

        os.unlink(f.name)

    def test_task_enabled_defaults_to_true(self):
        """Test that tasks are enabled by default."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  daily:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily summary"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)
            assert config.tasks["daily"].enabled is True

        os.unlink(f.name)

    def test_task_enabled_explicit_true(self):
        """Test explicitly setting enabled to true."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  daily:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily summary"
    enabled: true
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)
            assert config.tasks["daily"].enabled is True

        os.unlink(f.name)

    def test_task_enabled_false(self):
        """Test disabling a task with enabled: false."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  daily:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily summary"
    enabled: false
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)
            assert config.tasks["daily"].enabled is False

        os.unlink(f.name)

    def test_mixed_enabled_disabled_tasks(self):
        """Test config with both enabled and disabled tasks."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  active-task:
    schedule: "0 9 * * *"
    group: main
    prompt: "Active task"
    enabled: true

  paused-task:
    schedule: "0 10 * * *"
    group: main
    prompt: "Paused task"
    enabled: false

  default-task:
    schedule: "0 11 * * *"
    group: main
    prompt: "Default enabled"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)
            assert config.tasks["active-task"].enabled is True
            assert config.tasks["paused-task"].enabled is False
            assert config.tasks["default-task"].enabled is True

        os.unlink(f.name)


class TestQueueConfig:
    """Tests for queue configuration."""

    def test_queue_config_defaults(self):
        """Test QueueConfig default values."""
        config = QueueConfig()
        assert config.enabled is True
        assert config.heartbeat_interval == 300
        assert config.max_dispatch == 5

    def test_load_queue_config_defaults(self):
        """Test that queue config uses defaults when not specified."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  daily:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily summary"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)
            assert config.queue.enabled is True
            assert config.queue.heartbeat_interval == 300
            assert config.queue.max_dispatch == 5

        os.unlink(f.name)

    def test_load_queue_config_custom(self):
        """Test loading custom queue configuration."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  daily:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily summary"

queue:
  enabled: true
  heartbeat_interval: 60
  max_dispatch: 10
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)
            assert config.queue.enabled is True
            assert config.queue.heartbeat_interval == 60
            assert config.queue.max_dispatch == 10

        os.unlink(f.name)

    def test_load_queue_config_disabled(self):
        """Test loading queue config with enabled: false."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  daily:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily summary"

queue:
  enabled: false
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)
            assert config.queue.enabled is False
            # Other defaults should still apply
            assert config.queue.heartbeat_interval == 300
            assert config.queue.max_dispatch == 5

        os.unlink(f.name)

    def test_load_queue_config_partial(self):
        """Test loading queue config with partial options."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  daily:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily summary"

queue:
  heartbeat_interval: 120
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)
            # Specified value
            assert config.queue.heartbeat_interval == 120
            # Defaults for unspecified
            assert config.queue.enabled is True
            assert config.queue.max_dispatch == 5

        os.unlink(f.name)


class TestRemoteServerConfig:
    """Tests for remote server configuration."""

    def test_remote_server_config_defaults(self):
        """Test default values for RemoteServerConfig."""
        from openpaws.config import RemoteServerConfig

        config = RemoteServerConfig()
        assert config.enabled is False
        assert config.port_start == 18000
        assert config.port_end == 18100

    def test_load_remote_server_config_defaults(self):
        """Test that remote_servers uses defaults when not specified."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  daily:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily summary"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)
            assert config.remote_servers.enabled is False
            assert config.remote_servers.port_start == 18000
            assert config.remote_servers.port_end == 18100

        os.unlink(f.name)

    def test_load_remote_server_config_enabled(self):
        """Test loading remote_servers config when enabled."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  daily:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily summary"

remote_servers:
  enabled: true
  port_start: 19000
  port_end: 19100
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)
            assert config.remote_servers.enabled is True
            assert config.remote_servers.port_start == 19000
            assert config.remote_servers.port_end == 19100

        os.unlink(f.name)

    def test_load_remote_server_config_partial(self):
        """Test loading remote_servers config with partial options."""
        config_content = """
channels:
  telegram:
    bot_token: "test-token"

groups:
  main:
    channel: telegram
    chat_id: "123"

tasks:
  daily:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily summary"

remote_servers:
  enabled: true
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_content)
            f.flush()

            config = load_config(f.name)
            assert config.remote_servers.enabled is True
            # Defaults for unspecified
            assert config.remote_servers.port_start == 18000
            assert config.remote_servers.port_end == 18100

        os.unlink(f.name)
