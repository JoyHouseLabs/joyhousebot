"""Plugins package with bridge + native runtimes."""

from .bridge.host_client import PluginHostClient
from .core.contracts import BridgeRuntime, NativeRuntime
from .core.types import PluginHostError, PluginRecord, PluginSnapshot
from .manager import PluginManager, get_plugin_manager, initialize_plugins_for_workspace
from .native.loader import NativePluginLoader

__all__ = [
    "BridgeRuntime",
    "NativeRuntime",
    "PluginHostClient",
    "PluginHostError",
    "PluginManager",
    "NativePluginLoader",
    "PluginRecord",
    "PluginSnapshot",
    "get_plugin_manager",
    "initialize_plugins_for_workspace",
]

