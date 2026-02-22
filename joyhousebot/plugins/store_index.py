"""Store index protocol: fetch and parse plugin store index for online install (reserved)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


INDEX_VERSION = "1.0"


@dataclass
class Compatibility:
    min_platform_version: str = ""
    runtime: list[str] = field(default_factory=list)


@dataclass
class PackageEntry:
    id: str
    name: str
    version: str
    download_url: str
    description: str = ""
    signature_url: str = ""
    compatibility: Compatibility | None = None
    manifest_snippet: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoreIndex:
    version: str
    packages: list[PackageEntry]
    updated_at: str = ""
    next_page: str = ""


def _parse_compatibility(raw: Any) -> Compatibility | None:
    if not isinstance(raw, dict):
        return None
    return Compatibility(
        min_platform_version=str(raw.get("min_platform_version") or ""),
        runtime=[str(x) for x in raw.get("runtime") or [] if isinstance(raw.get("runtime"), list) else []],
    )


def _parse_package(raw: Any) -> PackageEntry | None:
    if not isinstance(raw, dict):
        return None
    pid = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    version = str(raw.get("version") or "").strip()
    download_url = str(raw.get("download_url") or "").strip()
    if not pid or not name or not version or not download_url:
        return None
    return PackageEntry(
        id=pid,
        name=name,
        version=version,
        download_url=download_url,
        description=str(raw.get("description") or ""),
        signature_url=str(raw.get("signature_url") or ""),
        compatibility=_parse_compatibility(raw.get("compatibility")),
        manifest_snippet=dict(raw.get("manifest_snippet") or {}) if isinstance(raw.get("manifest_snippet"), dict) else {},
    )


def parse_index(data: dict[str, Any]) -> StoreIndex:
    """Parse index JSON into StoreIndex. Raises ValueError if invalid."""
    version = str(data.get("version") or "").strip()
    if not version:
        raise ValueError("INDEX_INVALID: version required")
    raw_packages = data.get("packages")
    if not isinstance(raw_packages, list):
        raise ValueError("INDEX_INVALID: packages required and must be array")
    packages: list[PackageEntry] = []
    for raw in raw_packages:
        entry = _parse_package(raw)
        if entry:
            packages.append(entry)
    return StoreIndex(
        version=version,
        packages=packages,
        updated_at=str(data.get("updated_at") or ""),
        next_page=str(data.get("next_page") or ""),
    )


def load_index_from_path(path: Path) -> StoreIndex:
    """Load and parse index from a local file."""
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("INDEX_INVALID: root must be object")
    return parse_index(data)


def load_index_from_json_string(text: str) -> StoreIndex:
    """Load and parse index from JSON string (e.g. from HTTP response)."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("INDEX_INVALID: root must be object")
    return parse_index(data)
