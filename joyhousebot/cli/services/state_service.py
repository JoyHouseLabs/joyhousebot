"""Local JSON state persistence helpers for CLI services."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from joyhousebot.config.loader import get_data_dir


class StateService:
    """Reads/writes local JSON state files under data dir."""

    def __init__(self, namespace: str = "compat"):
        self._base = get_data_dir() / namespace
        self._base.mkdir(parents=True, exist_ok=True)

    def read_json(self, name: str, default: Any) -> Any:
        path = self._base / f"{name}.json"
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def write_json(self, name: str, data: Any) -> Path:
        path = self._base / f"{name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def path_of(self, name: str) -> Path:
        return self._base / f"{name}.json"

    def json_hash(self, data: Any) -> str:
        canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

