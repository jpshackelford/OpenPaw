"""Configuration loading and validation."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ChannelConfig:
    """Configuration for a channel (Telegram, Slack, etc.)."""
    
    type: str
    bot_token: str | None = None
    app_token: str | None = None


@dataclass
class GroupConfig:
    """Configuration for a group/conversation."""
    
    name: str
    channel: str
    chat_id: str
    trigger: str = "@paw"
    admin: bool = False
    mounts: list[str] = field(default_factory=list)


@dataclass
class TaskConfig:
    """Configuration for a scheduled task."""
    
    name: str
    schedule: str  # Cron expression
    group: str
    prompt: str


@dataclass 
class AgentConfig:
    """Configuration for the agent."""
    
    model: str = "anthropic/claude-sonnet-4-20250514"
    llm_proxy: str | None = None


@dataclass
class Config:
    """Root configuration."""
    
    channels: dict[str, ChannelConfig] = field(default_factory=dict)
    groups: dict[str, GroupConfig] = field(default_factory=dict)
    tasks: dict[str, TaskConfig] = field(default_factory=dict)
    agent: AgentConfig = field(default_factory=AgentConfig)


def expand_env_vars(value: str) -> str:
    """Expand ${VAR} patterns in strings."""
    if not isinstance(value, str):
        return value
    
    import re
    pattern = r'\$\{([^}]+)\}'
    
    def replacer(match):
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))
    
    return re.sub(pattern, replacer, value)


def expand_env_vars_recursive(obj):
    """Recursively expand env vars in a dict/list structure."""
    if isinstance(obj, dict):
        return {k: expand_env_vars_recursive(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [expand_env_vars_recursive(item) for item in obj]
    elif isinstance(obj, str):
        return expand_env_vars(obj)
    else:
        return obj


def load_config(path: Path | str | None = None) -> Config:
    """Load configuration from YAML file."""
    if path is None:
        path = Path.home() / ".openpaws" / "config.yaml"
    else:
        path = Path(path)
    
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    
    with open(path) as f:
        raw = yaml.safe_load(f)
    
    # Expand environment variables
    raw = expand_env_vars_recursive(raw)
    
    # Parse channels
    channels = {}
    for name, cfg in raw.get("channels", {}).items():
        channels[name] = ChannelConfig(type=name, **cfg)
    
    # Parse groups
    groups = {}
    for name, cfg in raw.get("groups", {}).items():
        groups[name] = GroupConfig(name=name, **cfg)
    
    # Parse tasks
    tasks = {}
    for name, cfg in raw.get("tasks", {}).items():
        tasks[name] = TaskConfig(name=name, **cfg)
    
    # Parse agent
    agent_cfg = raw.get("agent", {})
    agent = AgentConfig(**agent_cfg)
    
    return Config(
        channels=channels,
        groups=groups,
        tasks=tasks,
        agent=agent,
    )
