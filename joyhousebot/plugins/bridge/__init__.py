"""Bridge runtime for OpenClaw-compatible Node plugin host."""

from .manager import BridgePluginManager
from .skills import resolve_bridge_plugin_skill_dirs

__all__ = ["BridgePluginManager", "resolve_bridge_plugin_skill_dirs"]

