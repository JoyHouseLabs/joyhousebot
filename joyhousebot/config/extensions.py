"""Extension configuration helpers shared by submission and workers."""

from __future__ import annotations

from typing import Any

from joyhousebot.extension_discovery import installed_extensions


def enabled_extension_ids(config: Any, *, prefix: str | None = None) -> set[str]:
    """Return exact explicitly enabled extension release ids."""
    extensions = getattr(config, "extensions", None)
    values = {
        str(item).strip()
        for item in getattr(extensions, "enabled", ()) or ()
        if str(item).strip()
    }
    return {item for item in values if item.startswith(prefix)} if prefix else values


def enabled_channel_ids(config: Any) -> set[str]:
    """Return transport ids for explicitly enabled Channel extensions."""
    return {
        item.removeprefix("channel-")
        for item in enabled_extension_ids(config, prefix="channel-")
        if len(item) > len("channel-")
    }


def enabled_capability_ids(config: Any) -> set[str]:
    """Return exact capability extension ids used as entry-point names."""
    return enabled_extension_ids(config, prefix="capability-")


def enabled_channel_extension_ids(config: Any) -> set[str]:
    return enabled_extension_ids(config, prefix="channel-")


def enabled_connector_ids(config: Any) -> set[str]:
    return enabled_extension_ids(config, prefix="connector-")


def enabled_provider_extension_ids(config: Any) -> set[str]:
    return enabled_extension_ids(config, prefix="provider-")


def extension_settings(config: Any, extension_id: str) -> dict[str, Any]:
    extensions = getattr(config, "extensions", None)
    settings = getattr(extensions, "settings", {}) or {}
    value = settings.get(extension_id, {})
    return dict(value) if isinstance(value, dict) else {}


def installed_channel_ids() -> list[str]:
    """Inspect entry-point metadata without importing extension code."""
    return sorted(
        item.extension_id.removeprefix("channel-")
        for item in installed_extensions()
        if item.extension_type == "channel"
    )


__all__ = [
    "enabled_capability_ids",
    "enabled_channel_extension_ids",
    "enabled_channel_ids",
    "enabled_connector_ids",
    "enabled_extension_ids",
    "enabled_provider_extension_ids",
    "extension_settings",
    "installed_channel_ids",
]
