"""Cached configuration access facade."""

from __future__ import annotations

import threading
from pathlib import Path

from joyhousebot.config.loader import get_config_path, load_config
from joyhousebot.config.schema import Config

_lock = threading.RLock()
_cache: dict[str, Config] = {}


def _cache_key(config_path: Path | None = None) -> str:
    path = (
        Path(config_path).expanduser().resolve()
        if config_path
        else get_config_path().expanduser().resolve()
    )
    return str(path)


def get_config(*, config_path: Path | None = None, force_reload: bool = False) -> Config:
    """Get config with process-local cache and optional refresh."""
    key = _cache_key(config_path)
    with _lock:
        if force_reload or key not in _cache:
            # Preserve whether the caller explicitly selected a path. The
            # loader uses that distinction to fail on a missing deployment
            # file while still allowing defaults when ~/.joyhousebot has
            # never been initialized.
            _cache[key] = load_config(config_path)
        return _cache[key]
