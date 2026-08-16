"""Metadata-only discovery and explicit extension loading."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from hashlib import sha256
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Iterable

from porthouse.contracts.extensions import ExtensionManifest

ENTRY_POINT_GROUPS = {
    "porthouse.capabilities": "capability",
    "porthouse.channels": "channel",
    "porthouse.model_providers": "model_provider",
    "porthouse.tool_connectors": "tool_connector",
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


@dataclass(frozen=True, slots=True)
class ExtensionInventoryCandidate:
    """Safe metadata projection of a local source and/or installed wheel."""

    extension_id: str
    name: str
    description: str
    source_version: str
    extension_types: tuple[str, ...]
    distribution_name: str
    distribution_version: str
    source_location: str
    source_digest: str
    source_available: bool
    installed: bool
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "extension_id": self.extension_id,
            "name": self.name,
            "description": self.description,
            "source_version": self.source_version,
            "extension_types": list(self.extension_types),
            "distribution_name": self.distribution_name,
            "distribution_version": self.distribution_version,
            "source_location": self.source_location,
            "source_digest": self.source_digest,
            "source_available": self.source_available,
            "installed": self.installed,
            "metadata": dict(self.metadata),
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


def scan_extension_catalog(
    directories: Iterable[str | Path],
    *,
    installed: Iterable[InstalledExtension] | None = None,
) -> list[ExtensionInventoryCandidate]:
    """Scan extension package metadata without importing extension modules.

    Only direct child ``pyproject.toml`` files and installed entry-point
    metadata are read. Entry-point targets are recorded as inert strings.
    """
    discovered: dict[str, dict[str, Any]] = {}
    for raw_directory in directories:
        directory = Path(raw_directory).expanduser().resolve()
        if not directory.is_dir():
            continue
        for project_file in sorted(directory.glob("*/pyproject.toml")):
            payload = project_file.read_bytes()
            project = dict(tomllib.loads(payload.decode("utf-8")).get("project") or {})
            groups = dict(project.get("entry-points") or {})
            for group, extension_type in ENTRY_POINT_GROUPS.items():
                entries = dict(groups.get(group) or {})
                for extension_id, target in sorted(entries.items()):
                    normalized = str(extension_id).strip()
                    if not normalized:
                        continue
                    value = discovered.setdefault(
                        normalized,
                        {
                            "extension_id": normalized,
                            "name": str(project.get("name") or normalized),
                            "description": str(project.get("description") or ""),
                            "source_version": str(project.get("version") or ""),
                            "extension_types": set(),
                            "distribution_name": str(project.get("name") or ""),
                            "distribution_version": "",
                            "source_locations": [],
                            "source_digests": [],
                            "entry_points": {},
                            "installed": False,
                        },
                    )
                    location = str(project_file.parent)
                    digest = f"sha256:{sha256(payload).hexdigest()}"
                    if location not in value["source_locations"]:
                        value["source_locations"].append(location)
                    if digest not in value["source_digests"]:
                        value["source_digests"].append(digest)
                    value["extension_types"].add(extension_type)
                    value["entry_points"][group] = str(target)

    installed_values = list(installed if installed is not None else installed_extensions())
    for item in installed_values:
        value = discovered.setdefault(
            item.extension_id,
            {
                "extension_id": item.extension_id,
                "name": item.distribution_name or item.extension_id,
                "description": "",
                "source_version": "",
                "extension_types": set(),
                "distribution_name": item.distribution_name,
                "distribution_version": item.distribution_version,
                "source_locations": [],
                "source_digests": [],
                "entry_points": {},
                "installed": True,
            },
        )
        value["installed"] = True
        value["extension_types"].add(item.extension_type)
        if not value["distribution_name"]:
            value["distribution_name"] = item.distribution_name
        value["distribution_version"] = item.distribution_version

    output = []
    for extension_id in sorted(discovered):
        value = discovered[extension_id]
        locations = sorted(value["source_locations"])
        digests = sorted(value["source_digests"])
        output.append(
            ExtensionInventoryCandidate(
                extension_id=extension_id,
                name=str(value["name"]),
                description=str(value["description"]),
                source_version=str(value["source_version"]),
                extension_types=tuple(sorted(value["extension_types"])),
                distribution_name=str(value["distribution_name"]),
                distribution_version=str(value["distribution_version"]),
                source_location=locations[0] if locations else "",
                source_digest=digests[0] if digests else "",
                source_available=bool(locations or value["installed"]),
                installed=bool(value["installed"]),
                metadata={
                    "entry_points": dict(value["entry_points"]),
                    "source_locations": locations,
                    "source_digests": digests,
                    "source_conflict": len(locations) > 1 or len(digests) > 1,
                },
            )
        )
    return output


__all__ = [
    "ENTRY_POINT_GROUPS",
    "ExtensionInventoryCandidate",
    "InstalledExtension",
    "enabled_entry_points",
    "entry_points",
    "installed_extensions",
    "scan_extension_catalog",
    "validate_manifest",
]
