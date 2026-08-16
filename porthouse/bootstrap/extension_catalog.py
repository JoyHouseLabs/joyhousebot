"""Deployment-time extension inventory and immutable release discovery."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from porthouse.capabilities import CapabilityRegistry
from porthouse.channels.plugins import ChannelRegistry
from porthouse.config.extensions import (
    deployment_allowed_extension_ids,
    enabled_capability_ids,
    enabled_channel_extension_ids,
    enabled_connector_ids,
    initially_active_extension_ids,
)
from porthouse.config.loader import get_config_path
from porthouse.connectors import ToolConnectorRegistry
from porthouse.extension_discovery import scan_extension_catalog
from porthouse.providers.registry import get_provider_registry
from porthouse.storage.factory import create_runtime_store


def configured_catalog_directories(config: Any) -> list[Path]:
    """Resolve relative catalog directories next to the selected config file."""
    configured = getattr(getattr(config, "extensions", None), "catalog_directories", ())
    base = get_config_path().expanduser().resolve().parent
    values = []
    for item in configured or ():
        path = Path(str(item)).expanduser()
        values.append((base / path).resolve() if not path.is_absolute() else path.resolve())
    return values


def synchronize_extension_inventory(
    config: Any, *, store: Any | None = None
) -> list[dict[str, Any]]:
    """Persist filesystem/distribution metadata without importing extension code."""
    runtime_store = store or create_runtime_store(config)
    owns_store = store is None
    try:
        candidates = scan_extension_catalog(configured_catalog_directories(config))
        return runtime_store.sync_extension_inventory(
            [item.to_dict() for item in candidates],
            allowed_ids=deployment_allowed_extension_ids(config),
            initially_active_ids=initially_active_extension_ids(config),
        )
    finally:
        if owns_store:
            runtime_store.close()


def discover_enabled_extensions(
    config: Any, *, store: Any | None = None
) -> list[dict[str, str]]:
    """Persist immutable manifests/components without starting an execution Worker."""
    runtime_store = store or create_runtime_store(config)
    owns_store = store is None
    discovered: dict[str, dict[str, str]] = {}
    try:
        synchronize_extension_inventory(config, store=runtime_store)
        capabilities = CapabilityRegistry(
            store=runtime_store,
            enabled_plugins=enabled_capability_ids(config),
        )
        for manifest in capabilities.plugins.manifests():
            discovered[manifest.plugin_id] = {
                "extension_id": manifest.plugin_id,
                "version": manifest.version,
                "type": "capability",
            }

        for manifest in get_provider_registry(config).manifests():
            runtime_store.upsert_plugin_release(manifest.to_release_dict())
            discovered[manifest.extension_id] = {
                "extension_id": manifest.extension_id,
                "version": manifest.version,
                "type": "model_provider",
            }

        connectors = ToolConnectorRegistry()
        connectors.load_entry_points(enabled=enabled_connector_ids(config))
        for manifest in connectors.manifests():
            runtime_store.upsert_plugin_release(manifest.to_release_dict())
            discovered[manifest.extension_id] = {
                "extension_id": manifest.extension_id,
                "version": manifest.version,
                "type": "connector",
            }

        channels = ChannelRegistry()
        channels.load_entry_points(enabled=enabled_channel_extension_ids(config))
        for manifest in channels.manifests():
            runtime_store.upsert_plugin_release(manifest.to_release_dict())
            discovered[manifest.extension_id] = {
                "extension_id": manifest.extension_id,
                "version": manifest.version,
                "type": "channel",
            }
        return [discovered[key] for key in sorted(discovered)]
    finally:
        if owns_store:
            runtime_store.close()


__all__ = [
    "configured_catalog_directories",
    "discover_enabled_extensions",
    "synchronize_extension_inventory",
]
