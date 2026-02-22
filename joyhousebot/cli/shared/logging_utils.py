"""Loguru helpers for consistent file logging in CLI commands."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

_SINK_IDS: dict[str, int] = {}


def ensure_rotating_log_file(name: str, level: str = "INFO") -> Path:
    """Ensure a rotating log sink for the given command name."""
    if name in _SINK_IDS:
        return Path.home() / ".joyhousebot" / "logs" / f"{name}.log"
    log_dir = Path.home() / ".joyhousebot" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name}.log"
    sink_id = logger.add(
        str(log_path),
        level=level,
        rotation="10 MB",
        retention="14 days",
        enqueue=True,
        encoding="utf-8",
        backtrace=False,
        diagnose=False,
    )
    _SINK_IDS[name] = sink_id
    return log_path

