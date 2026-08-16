"""Extension configuration helpers shared by submission and workers."""

from __future__ import annotations

from typing import Any

from porthouse.extension_discovery import installed_extensions


def deployment_allowed_extension_ids(
    config: Any, *, prefix: str | None = None
) -> set[str]:
    """Return extension ids that this deployment permits workers to import."""
    extensions = getattr(config, "extensions", None)
    explicit = getattr(extensions, "allowed_ids", ()) or ()
    legacy = getattr(extensions, "enabled", ()) or ()
    values = {
        str(item).strip()
        for item in (*explicit, *legacy)
        if str(item).strip()
    }
    return {item for item in values if item.startswith(prefix)} if prefix else values


def initially_active_extension_ids(config: Any) -> set[str]:
    """Return one-time activation seeds used only for new inventory rows."""
    extensions = getattr(config, "extensions", None)
    explicit = getattr(extensions, "initially_active", ()) or ()
    legacy = getattr(extensions, "enabled", ()) or ()
    return {
        str(item).strip()
        for item in (*explicit, *legacy)
        if str(item).strip()
    }


def enabled_extension_ids(config: Any, *, prefix: str | None = None) -> set[str]:
    """Compatibility alias for the deployment import allowlist."""
    return deployment_allowed_extension_ids(config, prefix=prefix)


def enabled_channel_ids(config: Any) -> set[str]:
    """Return transport ids for deployment-allowed Channel extensions."""
    return {
        item.removeprefix("channel-")
        for item in enabled_extension_ids(config, prefix="channel-")
        if len(item) > len("channel-")
    }


def enabled_capability_ids(config: Any) -> set[str]:
    """Return deployment-allowed capability entry-point names."""
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
    "deployment_allowed_extension_ids",
    "enabled_capability_ids",
    "enabled_channel_extension_ids",
    "enabled_channel_ids",
    "enabled_connector_ids",
    "enabled_extension_ids",
    "enabled_provider_extension_ids",
    "extension_settings",
    "initially_active_extension_ids",
    "installed_channel_ids",
]
