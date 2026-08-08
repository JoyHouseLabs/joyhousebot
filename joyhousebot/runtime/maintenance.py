"""Retention maintenance for the durable Agent runtime."""

from __future__ import annotations

import asyncio
import inspect
import os
import time

from loguru import logger

# How often the coordinator purges expired runtime data.
_PURGE_INTERVAL_SECONDS = 600.0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


class RuntimeMaintenanceMixin:
    async def _purge_old_runtime_data(self) -> None:
        """Periodically drop expired runtime rows; failures only get logged."""
        purge = getattr(self.store, "purge_old_runtime_data", None)
        if purge is None:
            return
        retention_days = _env_int("JOYHOUSEBOT_RETENTION_DAYS", 30)
        diagnostics_days = _env_int("JOYHOUSEBOT_DIAGNOSTICS_RETENTION_DAYS", retention_days)
        cutoff_ms = int((time.time() - retention_days * 86400) * 1000)
        diagnostics_cutoff_ms = int((time.time() - diagnostics_days * 86400) * 1000)
        try:
            # Store implementations perform blocking PostgreSQL deletes. Run
            # both sync and async implementations off the coordinator loop so
            # maintenance cannot pause lease heartbeats or task claiming.
            if inspect.iscoroutinefunction(purge):
                await asyncio.to_thread(asyncio.run, purge(cutoff_ms, diagnostics_cutoff_ms))
            else:
                await asyncio.to_thread(purge, cutoff_ms, diagnostics_cutoff_ms)
        except Exception:
            logger.exception("Runtime data purge failed")
