"""Read-only helpers: plugin root discovery, installed apps, and plugin tool names."""

from __future__ import annotations

from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "joyhousebot.plugin.json"


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def get_plugin_roots(workspace: Path | str, config: Any) -> list[Path]:
    """
    Return plugin root directories in a consistent order, deduplicated by resolve().
    Accepts config as object (with .plugins.load.paths) or dict (with ["plugins"]["load"]["paths"]).
    Order: config load.paths, workspace/.joyhouse/plugins, ~/.joyhouse/plugins, examples/native-plugins.
    """
    workspace_path = Path(workspace).expanduser().resolve()
    raw_paths: list[str] = []
    if hasattr(config, "plugins"):
        load_cfg = getattr(getattr(config, "plugins", None), "load", None)
        raw_paths = list(getattr(load_cfg, "paths", []) or [])
    elif isinstance(config, dict):
        load_cfg = _safe_dict(_safe_dict(config.get("plugins")).get("load"))
        raw_paths = [str(x).strip() for x in (load_cfg.get("paths") or []) if str(x).strip()]
    candidates: list[Path] = [Path(p).expanduser() for p in raw_paths if isinstance(p, str) and p.strip()]
    candidates.append(workspace_path / ".joyhouse" / "plugins")
    candidates.append(Path.home() / ".joyhouse" / "plugins")
    _examples = Path(__file__).resolve().parent.parent.parent / "examples" / "native-plugins"
    if _examples.exists() and _examples.is_dir():
        candidates.append(_examples)
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.exists():
            continue
        if candidate.is_file() and candidate.name == MANIFEST_FILENAME:
            r = candidate.parent.resolve()
            key = str(r)
            if key not in seen:
                seen.add(key)
                roots.append(r)
        elif candidate.is_dir() and (candidate / MANIFEST_FILENAME).exists():
            r = candidate.resolve()
            key = str(r)
            if key not in seen:
                seen.add(key)
                roots.append(r)
        elif candidate.is_dir():
            for child in candidate.iterdir():
                if child.is_dir() and (child / MANIFEST_FILENAME).exists():
                    r = child.resolve()
                    key = str(r)
                    if key not in seen:
                        seen.add(key)
                        roots.append(r)
    return roots


def get_installed_apps_for_agent(workspace: Path, config: Any) -> list[dict[str, Any]]:
    """
    Return enabled plugin apps for agent context (app_id, name, route).
    Use only installed and enabled apps; do not guess app_id.
    """
    try:
        from joyhousebot.plugins.apps import resolve_plugin_apps

        apps = resolve_plugin_apps(workspace, config)
        enabled = [a for a in apps if a.get("enabled")]
        return [
            {
                "app_id": a.get("app_id", ""),
                "name": a.get("name", ""),
                "route": a.get("route", ""),
            }
            for a in enabled
        ]
    except Exception:
        return []


def get_plugin_tool_names_for_agent() -> list[str]:
    """
    Return list of plugin tool names (e.g. library.create_book) from loaded plugins.
    Use only these names with plugin.invoke; do not guess tool_name.
    """
    try:
        from joyhousebot.plugins.manager import get_plugin_manager

        snapshot = get_plugin_manager().status()
        names: list[str] = []
        for record in snapshot.plugins:
            names.extend(record.tool_names or [])
        return sorted(set(names))
    except Exception:
        return []
