"""Channel plugin registry and loader."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from joyhousebot.channels.plugins.types import ChannelPlugin, ChannelPluginFactory

if TYPE_CHECKING:
    from joyhousebot.bus.queue import MessageBus


class ChannelRegistry:
    """
    Registry for channel plugins.
    
    Supports:
    - Built-in channels (shipped with joyhousebot)
    - External plugins (via entry points or plugin directories)
    """
    
    def __init__(self) -> None:
        self._plugins: dict[str, ChannelPlugin] = {}
        self._factories: dict[str, ChannelPluginFactory] = {}
        self._configs: dict[str, Any] = {}
        self._builtin_channels = [
            "telegram",
            "discord", 
            "slack",
            "whatsapp",
            "feishu",
            "dingtalk",
            "mochat",
            "email",
            "qq",
        ]
    
    def register(self, channel_id: str, plugin: ChannelPlugin, override: bool = True) -> None:
        """
        Register a channel plugin instance.
        
        Args:
            channel_id: Unique channel identifier
            plugin: Plugin instance
            override: If True (default), replace existing plugin with same id
        """
        if channel_id in self._plugins and not override:
            logger.warning(f"Channel '{channel_id}' already registered and override=False, skipping")
            return
        self._plugins[channel_id] = plugin
        logger.debug(f"Registered channel plugin: {channel_id}")
    
    def register_factory(self, channel_id: str, factory: ChannelPluginFactory) -> None:
        """Register a factory function for lazy instantiation."""
        self._factories[channel_id] = factory
    
    def get(self, channel_id: str) -> ChannelPlugin | None:
        """Get a registered channel plugin."""
        if channel_id in self._plugins:
            return self._plugins[channel_id]
        
        if channel_id in self._factories:
            plugin = self._factories[channel_id]()
            self._plugins[channel_id] = plugin
            return plugin
        
        return None
    
    def list_channels(self) -> list[str]:
        """List all registered channel IDs."""
        all_ids = set(self._plugins.keys()) | set(self._factories.keys())
        return sorted(all_ids)
    
    def list_builtins(self) -> list[str]:
        """List built-in channel IDs."""
        return self._builtin_channels.copy()
    
    def load_builtin(self, channel_id: str) -> ChannelPlugin | None:
        """
        Load a built-in channel plugin by ID.
        
        Built-in channels are in joyhousebot.channels.plugins.builtin.<id>
        """
        if channel_id not in self._builtin_channels:
            logger.warning(f"Unknown built-in channel: {channel_id}")
            return None
        
        try:
            module_path = f"joyhousebot.channels.plugins.builtin.{channel_id}"
            module = importlib.import_module(module_path)
            
            if hasattr(module, "create_plugin"):
                plugin = module.create_plugin()
                self._plugins[channel_id] = plugin
                logger.info(f"Loaded built-in channel: {channel_id}")
                return plugin
            else:
                logger.error(f"Module {module_path} has no create_plugin function")
                return None
                
        except ImportError as e:
            logger.error(f"Failed to import channel {channel_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to load channel {channel_id}: {e}")
            return None
    
    def load_all_builtins(self) -> dict[str, ChannelPlugin]:
        """Load all available built-in channels."""
        loaded = {}
        for channel_id in self._builtin_channels:
            plugin = self.load_builtin(channel_id)
            if plugin:
                loaded[channel_id] = plugin
        return loaded
    
    def load_from_directory(self, path: Path) -> list[str]:
        """
        Load channel plugins from a directory.
        
        Each plugin should be a subdirectory with a plugin.py file
        that defines a create_plugin() function.
        """
        loaded = []
        plugin_dir = Path(path)
        
        if not plugin_dir.exists():
            logger.warning(f"Plugin directory does not exist: {path}")
            return loaded
        
        for item in plugin_dir.iterdir():
            if not item.is_dir():
                continue
            
            plugin_file = item / "plugin.py"
            if not plugin_file.exists():
                continue
            
            try:
                spec = importlib.util.spec_from_file_location(
                    f"channel_plugin_{item.name}",
                    plugin_file
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    
                    if hasattr(module, "create_plugin"):
                        plugin = module.create_plugin()
                        channel_id = plugin.id
                        self._plugins[channel_id] = plugin
                        loaded.append(channel_id)
                        logger.info(f"Loaded channel plugin from {item}: {channel_id}")
                    else:
                        logger.warning(f"Plugin {item} has no create_plugin function")
                        
            except Exception as e:
                logger.error(f"Failed to load plugin from {item}: {e}")
        
        return loaded
    
    def set_config(self, channel_id: str, config: Any) -> None:
        """Store configuration for a channel."""
        self._configs[channel_id] = config
    
    def get_config(self, channel_id: str) -> Any | None:
        """Get stored configuration for a channel."""
        return self._configs.get(channel_id)
    
    def is_loaded(self, channel_id: str) -> bool:
        """Check if a channel plugin is loaded."""
        return channel_id in self._plugins
    
    def unload(self, channel_id: str) -> bool:
        """Remove a channel from registry."""
        if channel_id in self._plugins:
            del self._plugins[channel_id]
            return True
        return False


_registry: ChannelRegistry | None = None


def get_channel_registry() -> ChannelRegistry:
    """Get the global channel registry singleton."""
    global _registry
    if _registry is None:
        _registry = ChannelRegistry()
    return _registry


def reset_channel_registry() -> None:
    """Reset the global registry (for testing)."""
    global _registry
    _registry = None
