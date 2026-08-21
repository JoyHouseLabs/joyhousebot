"""Extension configuration helpers shared by submission and workers."""

from __future__ import annotations

from typing import Any

from joyhousebot.extension_discovery import installed_extensions


def deployment_allowed_extension_ids(
    config: Any, *, prefix: str | None = None
) -> set[str]:
    """Return extension ids that this deployment permits workers to import."""
    extensions = getattr(config, "extensions", None)
    explicit = getattr(extensions, "allowed_ids", ()) or ()
    values = {
        str(item).strip()
        for item in explicit
        if str(item).strip()
    }
    return {item for item in values if item.startswith(prefix)} if prefix else values


def initially_active_extension_ids(config: Any) -> set[str]:
    """Return one-time activation seeds used only for new inventory rows."""
    extensions = getattr(config, "extensions", None)
    explicit = getattr(extensions, "initially_active", ()) or ()
    return {
        str(item).strip()
        for item in explicit
        if str(item).strip()
    }


def allowed_channel_ids(config: Any) -> set[str]:
    """Return transport ids for deployment-allowed Channel extensions."""
    return {
        item.removeprefix("channel-")
        for item in deployment_allowed_extension_ids(config, prefix="channel-")
        if len(item) > len("channel-")
    }


def allowed_capability_extension_ids(config: Any) -> set[str]:
    """Return deployment-allowed capability entry-point names."""
    return deployment_allowed_extension_ids(config, prefix="capability-")


def allowed_channel_extension_ids(config: Any) -> set[str]:
    return deployment_allowed_extension_ids(config, prefix="channel-")


def allowed_connector_extension_ids(config: Any) -> set[str]:
    return deployment_allowed_extension_ids(config, prefix="connector-")


def allowed_provider_extension_ids(config: Any) -> set[str]:
    return deployment_allowed_extension_ids(config, prefix="provider-")


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
    "allowed_capability_extension_ids",
    "allowed_channel_extension_ids",
    "allowed_channel_ids",
    "allowed_connector_extension_ids",
    "allowed_provider_extension_ids",
    "extension_settings",
    "initially_active_extension_ids",
    "installed_channel_ids",
]
