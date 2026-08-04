"""Channel plugin registry and loader."""

from __future__ import annotations

import importlib

from loguru import logger

from joyhousebot.channels.plugins.types import ChannelPlugin


class ChannelRegistry:
    """Registry for the channel adapters compiled into joyhousebot."""

    def __init__(self) -> None:
        self._plugins: dict[str, ChannelPlugin] = {}
        self._builtin_channels = [
            "telegram",
            "discord",
            "slack",
            "whatsapp",
            "feishu",
            "dingtalk",
            "email",
            "qq",
        ]

    def get(self, channel_id: str) -> ChannelPlugin | None:
        """Get a registered channel plugin."""
        return self._plugins.get(channel_id)

    def list_channels(self) -> list[str]:
        """List all registered channel IDs."""
        return sorted(self._plugins)

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
