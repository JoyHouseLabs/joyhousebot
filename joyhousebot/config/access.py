"""Cached configuration access facade."""

from __future__ import annotations

import threading
from pathlib import Path

from joyhousebot.config.loader import get_config_path, load_config
from joyhousebot.config.schema import Config

_lock = threading.RLock()
_cache: dict[str, Config] = {}


def _cache_key(config_path: Path | None = None) -> str:
    path = Path(config_path).expanduser().resolve() if config_path else get_config_path().expanduser().resolve()
    return str(path)


def get_config(*, config_path: Path | None = None, force_reload: bool = False) -> Config:
    """Get config with process-local cache and optional refresh."""
    key = _cache_key(config_path)
    with _lock:
        if force_reload or key not in _cache:
            _cache[key] = load_config(Path(key))
        return _cache[key]


def clear_config_cache(*, config_path: Path | None = None) -> None:
    """Clear cached config entry (or all cache entries)."""
    with _lock:
        if config_path is None:
            _cache.clear()
            return
        _cache.pop(_cache_key(config_path), None)

