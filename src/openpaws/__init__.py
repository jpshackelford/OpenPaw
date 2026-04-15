"""OpenPaws - A lightweight, always-on AI assistant."""

__version__ = "0.1.1"

# Lazy imports to avoid slow SDK import on every `import openpaws`
# Map: attribute name -> (module, name)
_LAZY_IMPORTS = {
    "ConversationRunner": ("openpaws.runner", "ConversationRunner"),
    "ConversationResult": ("openpaws.runner", "ConversationResult"),
    "Config": ("openpaws.config", "Config"),
    "load_config": ("openpaws.config", "load_config"),
    "Scheduler": ("openpaws.scheduler", "Scheduler"),
    "ScheduledTask": ("openpaws.scheduler", "ScheduledTask"),
}


def __getattr__(name: str):
    """Lazy import for heavy modules."""
    if name in _LAZY_IMPORTS:
        module_name, attr = _LAZY_IMPORTS[name]
        import importlib

        return getattr(importlib.import_module(module_name), attr)
    raise AttributeError(f"module 'openpaws' has no attribute {name!r}")


__all__ = [
    "Config",
    "ConversationResult",
    "ConversationRunner",
    "load_config",
    "ScheduledTask",
    "Scheduler",
]
