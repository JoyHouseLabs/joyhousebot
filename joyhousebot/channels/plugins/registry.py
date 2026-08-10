"""Channel extension registry and entry-point discovery."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from joyhousebot.channels.plugins.types import ChannelPlugin
from joyhousebot.contracts.extensions import ExtensionManifest
from joyhousebot.extension_discovery import enabled_entry_points, validate_manifest

CHANNEL_ENTRY_POINT_GROUP = "joyhousebot.channels"

class ChannelRegistry:
    """Discover installed Channel extensions without owning their lifecycle."""

    def __init__(self) -> None:
        self._plugins: dict[str, ChannelPlugin] = {}
        self._sources: dict[str, str] = {}

    def register(self, plugin: ChannelPlugin, *, source: str = "explicit") -> None:
        """Register one adapter and reject ambiguous channel ownership."""
        channel_id = str(getattr(plugin, "id", "")).strip()
        if not channel_id:
            raise ValueError("channel plugin id is required")
        if not callable(getattr(plugin, "start", None)) or not callable(
            getattr(plugin, "send", None)
        ):
            raise TypeError(f"channel plugin {channel_id} does not implement the Channel contract")

        manifest = getattr(plugin, "extension_manifest", None)
        if not isinstance(manifest, ExtensionManifest):
            raise TypeError(f"channel plugin {channel_id} must declare an extension manifest")
        if "channel" not in manifest.extension_types:
            raise ValueError(f"extension {manifest.extension_id} does not declare channel type")

        existing = self._plugins.get(channel_id)
        if existing is not None and existing is not plugin:
            raise ValueError(
                f"channel {channel_id} is already provided by {self._sources[channel_id]}"
            )
        self._plugins[channel_id] = plugin
        self._sources[channel_id] = source

    def get(self, channel_id: str) -> ChannelPlugin | None:
        """Get a registered channel plugin."""
        return self._plugins.get(channel_id)

    def list_channels(self) -> list[str]:
        """List installed and successfully loaded channel IDs."""
        return sorted(self._plugins)

    def source_for(self, channel_id: str) -> str | None:
        return self._sources.get(channel_id)

    def load_entry_points(
        self,
        *,
        enabled: Iterable[str],
        group: str = CHANNEL_ENTRY_POINT_GROUP,
    ) -> list[str]:
        """Load only explicitly enabled channel packages."""
        loaded: list[str] = []
        for entry in enabled_entry_points(group, enabled):
            exported = entry.load()
            plugin = self._plugin_from_export(exported, source=f"entry-point:{entry.name}")
            validate_manifest(
                plugin.extension_manifest,
                entry_name=str(entry.name),
                expected_type="channel",
            )
            self.register(plugin, source=f"entry-point:{entry.name}")
            loaded.append(plugin.extension_manifest.extension_id)
        return loaded

    def manifests(self) -> tuple[ExtensionManifest, ...]:
        return tuple(plugin.extension_manifest for plugin in self._plugins.values())

    @staticmethod
    def _plugin_from_export(exported: Any, *, source: str) -> ChannelPlugin:
        factory = getattr(exported, "create_plugin", None)
        if callable(factory):
            plugin = factory()
        elif callable(exported) and not hasattr(exported, "send"):
            plugin = exported()
        else:
            plugin = exported
        if plugin is None:
            raise TypeError(f"{source} did not return a channel plugin")
        return plugin


__all__ = [
    "CHANNEL_ENTRY_POINT_GROUP",
    "ChannelRegistry",
]
