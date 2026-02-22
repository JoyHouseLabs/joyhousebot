"""Backward-compatible skill directory resolver across bridge/native plugins."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from joyhousebot.plugins.bridge.skills import resolve_bridge_plugin_skill_dirs
from joyhousebot.plugins.native.skills import resolve_native_plugin_skill_dirs


def resolve_plugin_skill_dirs(workspace: Path, config: Any) -> list[Path]:
    """Return all enabled plugin skill directories from bridge + native manifests."""
    out: list[Path] = []
    seen: set[str] = set()
    for resolved in [
        *resolve_bridge_plugin_skill_dirs(workspace=workspace, config=config),
        *resolve_native_plugin_skill_dirs(workspace=workspace, config=config),
    ]:
        key = str(resolved.resolve())
        if key not in seen:
            seen.add(key)
            out.append(resolved)
    return out

