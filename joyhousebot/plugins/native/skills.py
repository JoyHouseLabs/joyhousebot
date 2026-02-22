"""Resolve skill directories declared by python-native plugin manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from joyhousebot.plugins.discovery import MANIFEST_FILENAME, get_plugin_roots


def resolve_native_plugin_skill_dirs(workspace: Path, config: Any) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for root in get_plugin_roots(workspace, config):
        manifest_path = root / MANIFEST_FILENAME
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(manifest, dict):
            continue
        if str(manifest.get("runtime") or "python-native").strip() != "python-native":
            continue
        for raw_dir in manifest.get("skills") or []:
            if not isinstance(raw_dir, str) or not raw_dir.strip():
                continue
            resolved = (root / raw_dir).resolve()
            key = str(resolved)
            if resolved.exists() and key not in seen:
                seen.add(key)
                out.append(resolved)
    return out

