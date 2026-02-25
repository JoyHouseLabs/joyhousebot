"""Plugin apps: list and resolve webapp manifests for frontend AppHost."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from joyhousebot.plugins.discovery import MANIFEST_FILENAME, get_plugin_roots

WEBAPP_MANIFEST = "manifest.json"


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def resolve_plugin_apps(workspace: Path, config: Any) -> list[dict[str, Any]]:
    """
    Return list of plugin webapps from manifests (id, name, route, entry, plugin_id, base_path).
    base_path is the plugin-relative webapp dist dir (e.g. webapp/dist) for static serving.
    """
    apps: list[dict[str, Any]] = []
    seen_app_ids: set[str] = set()
    enabled_set: set[str] | None = None
    # Priority: apps.enabled > plugins.apps_enabled; empty list = all enabled
    apps_cfg = getattr(config, "apps", None)
    if apps_cfg is not None and hasattr(apps_cfg, "enabled"):
        raw = list(getattr(apps_cfg, "enabled") or [])
        if len(raw) > 0:
            enabled_set = set(str(x) for x in raw)
    if enabled_set is None:
        plugins = getattr(config, "plugins", None)
        if plugins is not None and hasattr(plugins, "apps_enabled") and isinstance(getattr(plugins, "apps_enabled"), list):
            raw_plugins = getattr(plugins, "apps_enabled") or []
            if len(raw_plugins) > 0:
                enabled_set = set(str(x) for x in raw_plugins)

    for root in get_plugin_roots(workspace, config):
        manifest_path = root / MANIFEST_FILENAME
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(manifest, dict):
            continue
        plugin_id = str(manifest.get("id") or "").strip()
        if not plugin_id:
            continue
        webapp_cfg = _safe_dict(manifest.get("webapp"))
        if not webapp_cfg:
            continue
        webapp_path = Path(webapp_cfg.get("path") or "webapp")
        manifest_rel = str(webapp_cfg.get("manifest") or "webapp/manifest.json").strip()
        app_manifest_path = root / manifest_rel
        if not app_manifest_path.exists():
            continue
        try:
            app_manifest = json.loads(app_manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(app_manifest, dict):
            continue
        app_id = str(app_manifest.get("app_id") or plugin_id).strip()
        if not app_id or app_id in seen_app_ids:
            continue
        seen_app_ids.add(app_id)
        name = str(app_manifest.get("name") or app_id)
        route = str(app_manifest.get("route") or f"/app/{app_id}").strip()
        entry = str(app_manifest.get("entry") or "dist/index.html").strip()
        dist_dir = (root / webapp_path / "dist").resolve()
        enabled = enabled_set is None or app_id in enabled_set
        # Optional display fields from webapp manifest
        icon_raw = app_manifest.get("icon")
        icon = str(icon_raw).strip() if icon_raw else None
        description_raw = app_manifest.get("description")
        description = str(description_raw).strip() if description_raw else None
        activation_raw = app_manifest.get("activation_command")
        activation_command = str(activation_raw).strip() if activation_raw else None
        entry_dict: dict[str, Any] = {
            "app_id": app_id,
            "name": name,
            "route": route,
            "entry": entry,
            "plugin_id": plugin_id,
            "base_path": str(dist_dir) if dist_dir.exists() else "",
            "enabled": enabled,
        }
        if icon:
            entry_dict["icon"] = icon
        if description:
            entry_dict["description"] = description
        if activation_command:
            entry_dict["activation_command"] = activation_command
        apps.append(entry_dict)
    return apps
