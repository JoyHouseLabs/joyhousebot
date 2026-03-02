"""Plugins package with native Python plugin runtime."""

from .core.types import PluginHostError, PluginRecord, PluginSnapshot
from .manager import PluginManager, get_plugin_manager, initialize_plugins_for_workspace
from .native.loader import NativePluginLoader

__all__ = [
    "PluginHostError",
    "PluginManager",
    "NativePluginLoader",
    "PluginRecord",
    "PluginSnapshot",
    "get_plugin_manager",
    "initialize_plugins_for_workspace",
]
