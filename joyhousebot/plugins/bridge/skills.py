"""Resolve skill directories declared by OpenClaw plugin manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_manifest(root_dir: Path) -> dict[str, Any] | None:
    manifest_path = root_dir / "openclaw.plugin.json"
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _plugin_enabled(plugin_id: str, config: Any) -> bool:
    plugins = getattr(config, "plugins", None)
    if plugins is None:
        return False
    if not bool(getattr(plugins, "enabled", True)):
        return False
    deny = set(getattr(plugins, "deny", []) or [])
    if plugin_id in deny:
        return False
    allow = set(getattr(plugins, "allow", []) or [])
    if allow and plugin_id not in allow:
        return False
    entries = getattr(plugins, "entries", {}) or {}
    entry = entries.get(plugin_id) if isinstance(entries, dict) else None
    if entry is not None and hasattr(entry, "enabled"):
        return bool(getattr(entry, "enabled", True))
    return True


def _iter_candidate_plugin_roots(workspace: Path, config: Any) -> list[Path]:
    roots: list[Path] = []
    plugins = getattr(config, "plugins", None)
    if plugins is None:
        return roots
    load_cfg = getattr(plugins, "load", None)
    for raw in getattr(load_cfg, "paths", []) or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        candidate = Path(raw).expanduser()
        if candidate.exists():
            if candidate.is_file():
                roots.append(candidate.parent)
            else:
                roots.append(candidate)
    defaults = [
        workspace / ".openclaw" / "extensions",
        Path.home() / ".openclaw" / "extensions",
    ]
    for directory in defaults:
        if directory.exists():
            roots.append(directory)
    return roots


def resolve_bridge_plugin_skill_dirs(workspace: Path, config: Any) -> list[Path]:
    """Return all enabled plugin skill directories from bridge/OpenClaw manifests."""
    seen = set()
    out: list[Path] = []

    for root in _iter_candidate_plugin_roots(workspace, config):
        manifest = _read_manifest(root)
        manifests: list[tuple[Path, dict[str, Any]]] = []
        if manifest:
            manifests.append((root, manifest))
        else:
            for child in root.iterdir() if root.exists() else []:
                if not child.is_dir():
                    continue
                parsed = _read_manifest(child)
                if parsed:
                    manifests.append((child, parsed))
        for plugin_root, parsed in manifests:
            plugin_id = str(parsed.get("id") or "").strip()
            if not plugin_id or not _plugin_enabled(plugin_id, config):
                continue
            for raw_dir in parsed.get("skills") or []:
                if not isinstance(raw_dir, str) or not raw_dir.strip():
                    continue
                resolved = (plugin_root / raw_dir).resolve()
                if not resolved.exists() or str(resolved) in seen:
                    continue
                seen.add(str(resolved))
                out.append(resolved)
    return out

