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
    """Configuration for a scheduled task.
    
    Tasks can be scheduled in three ways (mutually exclusive):
    - schedule: Cron expression (e.g., "0 9 * * *")
    - interval: Run every N seconds (e.g., 3600 for every hour)
    - once: Run at a specific timestamp (ISO format or "YYYY-MM-DD HH:MM")
    """
    
    name: str
    group: str
    prompt: str
    schedule: str | None = None  # Cron expression
    interval: int | None = None  # Seconds between runs
    once: str | None = None  # ISO timestamp for one-time execution


@dataclass 
class AgentConfig:
    """Configuration for the agent."""

    model: str = "anthropic/claude-sonnet-4-20250514"
    llm_proxy: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    system_prompt: str | None = None


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


def _parse_channels(raw: dict) -> dict[str, ChannelConfig]:
    """Parse channel configurations from raw YAML data."""
    return {
        name: ChannelConfig(type=name, **cfg)
        for name, cfg in raw.get("channels", {}).items()
    }


def _parse_groups(raw: dict) -> dict[str, GroupConfig]:
    """Parse group configurations from raw YAML data."""
    return {
        name: GroupConfig(name=name, **cfg)
        for name, cfg in raw.get("groups", {}).items()
    }


_INTERVAL_MULTIPLIERS = {"h": 3600, "m": 60, "s": 1}


def _parse_interval(value: int | str) -> int:
    """Parse interval value to seconds (e.g., "1h", "30m", "60s")."""
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        raise ValueError(f"Invalid interval value: {value}")

    value = value.strip().lower()
    if value[-1] in _INTERVAL_MULTIPLIERS:
        return int(value[:-1]) * _INTERVAL_MULTIPLIERS[value[-1]]
    return int(value)


def _validate_task_schedule(name: str, cfg: dict) -> None:
    """Validate that a task has exactly one schedule type."""
    schedule_types = ["schedule", "interval", "once"]
    present = [t for t in schedule_types if t in cfg and cfg[t] is not None]
    
    if len(present) == 0:
        raise ValueError(f"Task '{name}' must have one of: schedule, interval, or once")
    if len(present) > 1:
        raise ValueError(f"Task '{name}' has multiple schedule types: {present}")


def _parse_tasks(raw: dict) -> dict[str, TaskConfig]:
    """Parse task configurations from raw YAML data."""
    tasks = {}
    for name, cfg in raw.get("tasks", {}).items():
        _validate_task_schedule(name, cfg)
        
        # Parse interval if present
        if "interval" in cfg and cfg["interval"] is not None:
            cfg["interval"] = _parse_interval(cfg["interval"])
        
        tasks[name] = TaskConfig(name=name, **cfg)
    return tasks


def _resolve_config_path(path: Path | str | None) -> Path:
    """Resolve and validate config file path."""
    if path is None:
        resolved = Path.home() / ".openpaws" / "config.yaml"
    else:
        resolved = Path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"Config file not found: {resolved}")
    return resolved


def load_config(path: Path | str | None = None) -> Config:
    """Load configuration from YAML file."""
    config_path = _resolve_config_path(path)

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    raw = expand_env_vars_recursive(raw)

    return Config(
        channels=_parse_channels(raw),
        groups=_parse_groups(raw),
        tasks=_parse_tasks(raw),
        agent=AgentConfig(**raw.get("agent", {})),
    )
