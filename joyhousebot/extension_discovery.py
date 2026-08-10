"""Metadata-only discovery and explicit extension loading."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from typing import Any, Iterable

from joyhousebot.contracts.extensions import ExtensionManifest

ENTRY_POINT_GROUPS = {
    "joyhousebot.capabilities": "capability",
    "joyhousebot.channels": "channel",
    "joyhousebot.model_providers": "model_provider",
    "joyhousebot.tool_connectors": "tool_connector",
}


@dataclass(frozen=True, slots=True)
class InstalledExtension:
    extension_id: str
    extension_type: str
    distribution_name: str
    distribution_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "extension_id": self.extension_id,
            "extension_type": self.extension_type,
            "distribution_name": self.distribution_name,
            "distribution_version": self.distribution_version,
        }


def entry_points(group: str) -> tuple[Any, ...]:
    values = importlib_metadata.entry_points()
    selected = (
        values.select(group=group)
        if hasattr(values, "select")
        else values.get(group, ())
    )
    return tuple(selected)


def enabled_entry_points(group: str, enabled: Iterable[str]) -> tuple[Any, ...]:
    """Select metadata before importing extension code."""
    allowed = {str(item).strip() for item in enabled if str(item).strip()}
    available = entry_points(group)
    selected = tuple(entry for entry in available if entry.name in allowed)
    missing = sorted(allowed - {str(entry.name) for entry in available})
    if missing:
        raise RuntimeError(
            f"enabled extensions are not installed for {group}: {', '.join(missing)}"
        )
    names = [str(entry.name) for entry in selected]
    if len(names) != len(set(names)):
        raise RuntimeError(f"duplicate extension entry point in {group}")
    return selected


def validate_manifest(
    manifest: ExtensionManifest,
    *,
    entry_name: str,
    expected_type: str,
) -> None:
    if not isinstance(manifest, ExtensionManifest):
        raise TypeError(f"extension {entry_name!r} did not declare an ExtensionManifest")
    if manifest.extension_id != entry_name:
        raise ValueError(
            f"entry point {entry_name!r} does not match extension id "
            f"{manifest.extension_id!r}"
        )
    if expected_type not in manifest.extension_types:
        raise ValueError(
            f"extension {entry_name!r} does not declare type {expected_type!r}"
        )


def installed_extensions() -> list[InstalledExtension]:
    """List distributions without executing any extension module."""
    result: dict[tuple[str, str], InstalledExtension] = {}
    for group, extension_type in ENTRY_POINT_GROUPS.items():
        for entry in entry_points(group):
            distribution = getattr(entry, "dist", None)
            name = str(getattr(distribution, "name", "") or "")
            version = str(getattr(distribution, "version", "") or "")
            item = InstalledExtension(
                extension_id=str(entry.name),
                extension_type=extension_type,
                distribution_name=name,
                distribution_version=version,
            )
            result[(item.extension_id, item.extension_type)] = item
    return sorted(result.values(), key=lambda item: (item.extension_type, item.extension_id))


__all__ = [
    "ENTRY_POINT_GROUPS",
    "InstalledExtension",
    "enabled_entry_points",
    "entry_points",
    "installed_extensions",
    "validate_manifest",
]
