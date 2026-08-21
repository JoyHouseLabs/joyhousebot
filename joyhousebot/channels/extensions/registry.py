"""Channel extension registry and entry-point discovery."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from joyhousebot.channels.extensions.types import ChannelExtension
from joyhousebot.contracts.extensions import ExtensionManifest
from joyhousebot.extension_discovery import allowed_entry_points, validate_manifest

CHANNEL_ENTRY_POINT_GROUP = "joyhousebot.channels"

class ChannelExtensionRegistry:
    """Discover installed Channel extensions without owning their lifecycle."""

    def __init__(self) -> None:
        self._extensions: dict[str, ChannelExtension] = {}
        self._sources: dict[str, str] = {}

    def register(self, extension: ChannelExtension, *, source: str = "explicit") -> None:
        """Register one adapter and reject ambiguous channel ownership."""
        channel_id = str(getattr(extension, "id", "")).strip()
        if not channel_id:
            raise ValueError("channel extension id is required")
        if not callable(getattr(extension, "start", None)) or not callable(
            getattr(extension, "send", None)
        ):
            raise TypeError(f"channel extension {channel_id} does not implement the Channel contract")

        manifest = getattr(extension, "extension_manifest", None)
        if not isinstance(manifest, ExtensionManifest):
            raise TypeError(f"channel extension {channel_id} must declare an extension manifest")
        if "channel" not in manifest.extension_types:
            raise ValueError(f"extension {manifest.extension_id} does not declare channel type")

        existing = self._extensions.get(channel_id)
        if existing is not None and existing is not extension:
            raise ValueError(
                f"channel {channel_id} is already provided by {self._sources[channel_id]}"
            )
        self._extensions[channel_id] = extension
        self._sources[channel_id] = source

    def get(self, channel_id: str) -> ChannelExtension | None:
        """Get a registered channel extension."""
        return self._extensions.get(channel_id)

    def list_channels(self) -> list[str]:
        """List installed and successfully loaded channel IDs."""
        return sorted(self._extensions)

    def source_for(self, channel_id: str) -> str | None:
        return self._sources.get(channel_id)

    def load_entry_points(
        self,
        *,
        allowed_ids: Iterable[str],
        group: str = CHANNEL_ENTRY_POINT_GROUP,
    ) -> list[str]:
        """Load only explicitly allowed channel packages."""
        loaded: list[str] = []
        for entry in allowed_entry_points(group, allowed_ids):
            exported = entry.load()
            extension = self._extension_from_export(exported, source=f"entry-point:{entry.name}")
            validate_manifest(
                extension.extension_manifest,
                entry_name=str(entry.name),
                expected_type="channel",
            )
            self.register(extension, source=f"entry-point:{entry.name}")
            loaded.append(extension.extension_manifest.extension_id)
        return loaded

    def manifests(self) -> tuple[ExtensionManifest, ...]:
        return tuple(extension.extension_manifest for extension in self._extensions.values())

    @staticmethod
    def _extension_from_export(exported: Any, *, source: str) -> ChannelExtension:
        factory = getattr(exported, "create_extension", None)
        if callable(factory):
            extension = factory()
        elif callable(exported) and not hasattr(exported, "send"):
            extension = exported()
        else:
            extension = exported
        if extension is None:
            raise TypeError(f"{source} did not return a channel extension")
        return extension


__all__ = [
    "CHANNEL_ENTRY_POINT_GROUP",
    "ChannelExtensionRegistry",
]
