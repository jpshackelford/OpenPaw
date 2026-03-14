"""OpenPaws - A lightweight, always-on AI assistant."""

__version__ = "0.1.0"

# Lazy imports to avoid slow SDK import on every `import openpaws`
# Use explicit imports when needed: `from openpaws.runner import ConversationRunner`


def __getattr__(name: str):
    """Lazy import for heavy modules."""
    if name in ("ConversationRunner", "ConversationResult"):
        from openpaws.runner import ConversationResult, ConversationRunner

        return ConversationRunner if name == "ConversationRunner" else ConversationResult

    if name in ("Config", "load_config"):
        from openpaws.config import Config, load_config

        return Config if name == "Config" else load_config

    if name in ("Scheduler", "ScheduledTask"):
        from openpaws.scheduler import ScheduledTask, Scheduler

        return Scheduler if name == "Scheduler" else ScheduledTask

    raise AttributeError(f"module 'openpaws' has no attribute {name!r}")


__all__ = [
    "Config",
    "ConversationResult",
    "ConversationRunner",
    "load_config",
    "ScheduledTask",
    "Scheduler",
]
