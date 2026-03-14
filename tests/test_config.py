"""Tests for configuration loading."""

import os
import tempfile

import pytest

from openpaws.config import expand_env_vars, load_config


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
