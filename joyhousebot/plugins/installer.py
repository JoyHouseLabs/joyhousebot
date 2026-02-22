"""Offline plugin package install: unpack, verify signature (hashes), allowlist, register."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "joyhousebot.plugin.json"
SIGNATURE_FILENAME = "SIGNATURE.json"


def _read_manifest(plugin_root: Path) -> dict[str, Any]:
    path = plugin_root / MANIFEST_FILENAME
    if not path.exists():
        return {}  # caller checks
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _verify_hashes(plugin_root: Path, hashes: dict[str, str]) -> tuple[bool, str]:
    """Verify each path in hashes (relative to plugin_root) has the given sha256 hex. Returns (ok, error_message)."""
    for rel_path, expected_hex in (hashes or {}).items():
        if not rel_path or not isinstance(expected_hex, str):
            continue
        full = (plugin_root / rel_path).resolve()
        if not full.is_file():
            return False, f"MANIFEST_MISSING or path escape: {rel_path}"
        if not str(full).startswith(str(plugin_root.resolve())):
            return False, "SIGNATURE_INVALID: path escape"
        try:
            h = hashlib.sha256(full.read_bytes()).hexdigest()
            if h.lower() != expected_hex.strip().lower():
                return False, f"SIGNATURE_INVALID: hash mismatch for {rel_path}"
        except Exception as e:
            return False, f"SIGNATURE_INVALID: {e}"
    return True, ""


def verify_signature(plugin_root: Path) -> tuple[bool, str]:
    """
    If SIGNATURE.json exists, verify hashes. No public-key crypto in MVP.
    Returns (ok, error_message).
    """
    path = plugin_root / SIGNATURE_FILENAME
    if not path.exists():
        return True, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"SIGNATURE_INVALID: {e}"
    if not isinstance(data, dict):
        return False, "SIGNATURE_INVALID: invalid format"
    hashes = data.get("hashes")
    if not isinstance(hashes, dict):
        return True, ""  # no hashes to verify
    return _verify_hashes(plugin_root, hashes)


def allowlist_allows(plugin_id: str, plugins_cfg: dict[str, Any]) -> bool:
    """True if plugin_id is allowed (allow empty = allow all; deny wins)."""
    deny = set(plugins_cfg.get("deny") or [])
    if plugin_id in deny:
        return False
    allow = set(plugins_cfg.get("allow") or [])
    if allow and plugin_id not in allow:
        return False
    return True


def install_package(
    source: Path,
    target_dir: Path,
    config: Any,
) -> dict[str, Any]:
    """
    Install a plugin package from source (directory or .zip) into target_dir.
    Validates manifest, optional signature, allowlist; copies files.
    Does NOT modify config; returns suggested updates and any error.

    Returns:
        {
          "ok": bool,
          "plugin_id": str | None,
          "install_path": str,
          "error": str | None,
          "config_updates": {"add_path": str, "add_allow": str} | None
        }
    """
    source = source.expanduser().resolve()
    target_dir = target_dir.expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    plugins_cfg = getattr(config, "plugins", None)
    plugins_cfg = plugins_cfg if plugins_cfg is not None else {}
    if not hasattr(plugins_cfg, "get"):
        plugins_cfg = getattr(plugins_cfg, "model_dump", lambda: {})() if plugins_cfg else {}

    if source.is_file() and source.suffix.lower() == ".zip":
        extract_to = target_dir / source.stem
        if extract_to.exists():
            shutil.rmtree(extract_to, ignore_errors=True)
        try:
            with zipfile.ZipFile(source, "r") as z:
                z.extractall(extract_to)
            plugin_root = extract_to
        except Exception as e:
            return {
                "ok": False,
                "plugin_id": None,
                "install_path": "",
                "error": f"Failed to extract zip: {e}",
                "config_updates": None,
            }
    elif source.is_dir():
        plugin_root = source
        if plugin_root != target_dir and not str(plugin_root).startswith(str(target_dir)):
            dest = target_dir / plugin_root.name
            if not dest.exists():
                shutil.copytree(plugin_root, dest)
            plugin_root = dest
    else:
        return {
            "ok": False,
            "plugin_id": None,
            "install_path": "",
            "error": "INVALID_REQUEST: source is not a directory or .zip file",
            "config_updates": None,
        }

    manifest = _read_manifest(plugin_root)
    if not manifest:
        return {
            "ok": False,
            "plugin_id": None,
            "install_path": str(plugin_root),
            "error": "MANIFEST_MISSING",
            "config_updates": None,
        }
    plugin_id = str(manifest.get("id") or "").strip()
    if not plugin_id:
        return {
            "ok": False,
            "plugin_id": None,
            "install_path": str(plugin_root),
            "error": "MANIFEST_INVALID: id required",
            "config_updates": None,
        }
    if not manifest.get("version") or not manifest.get("runtime"):
        return {
            "ok": False,
            "plugin_id": plugin_id,
            "install_path": str(plugin_root),
            "error": "MANIFEST_INVALID: version and runtime required",
            "config_updates": None,
        }

    ok, err = verify_signature(plugin_root)
    if not ok:
        return {
            "ok": False,
            "plugin_id": plugin_id,
            "install_path": str(plugin_root),
            "error": err,
            "config_updates": None,
        }

    if not allowlist_allows(plugin_id, plugins_cfg):
        return {
            "ok": False,
            "plugin_id": plugin_id,
            "install_path": str(plugin_root),
            "error": "PLUGIN_NOT_ALLOWED",
            "config_updates": None,
        }

    add_path = str(plugin_root)
    paths = list(plugins_cfg.get("load") or {}).get("paths") or []
    if hasattr(plugins_cfg.get("load"), "paths"):
        paths = list(getattr(plugins_cfg.load, "paths", []) or [])
    if add_path not in paths:
        paths = [*paths, add_path]
    allow_list = list(plugins_cfg.get("allow") or [])
    if hasattr(plugins_cfg, "allow"):
        allow_list = list(getattr(plugins_cfg, "allow", []) or [])
    if plugin_id not in allow_list:
        allow_list = [*allow_list, plugin_id]

    return {
        "ok": True,
        "plugin_id": plugin_id,
        "install_path": str(plugin_root),
        "error": None,
        "config_updates": {
            "add_path": add_path,
            "add_allow": plugin_id,
            "paths": paths,
            "allow": allow_list,
        },
    }
