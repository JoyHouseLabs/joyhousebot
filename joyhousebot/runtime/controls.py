"""RuntimeControls for the durable Agent runtime."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from joyhousebot.runtime.models import (
    AgentEvent,
    EventType,
    RunStatus,
)


class RuntimeControlsMixin:
    async def cancel(self, run_id: str, reason: str = "cancelled by user") -> bool:
        record = await asyncio.to_thread(self.store.get_runtime_run, run_id)
        if record is None or record.status in {"completed", "failed", "cancelled", "timed_out"}:
            return False
        active = await self.supervisor.cancel(run_id, reason)
        if not active:
            await asyncio.to_thread(self.store.cancel_runtime_tasks, run_id)
            await self._finish_error(
                run_id,
                RunStatus.CANCELLED,
                EventType.RUN_CANCELLED,
                reason,
                record.started_at or record.created_at,
            )
        return True

    async def resume(self, run_id: str) -> Any:
        reset = await asyncio.to_thread(self.store.reset_runtime_run, run_id)
        if not reset:
            raise ValueError("only failed, cancelled, or timed out runs can be resumed")
        record = await asyncio.to_thread(self.store.get_runtime_run, run_id)
        if record and record.kind == "graph":
            await asyncio.to_thread(self.store.reset_runtime_tasks, run_id)
        await self.events.publish(
            AgentEvent(run_id=run_id, type=EventType.RUN_QUEUED.value, data={"resumed": True})
        )
        if self.worker_enabled:
            await self._schedule_record(run_id)
        else:
            await asyncio.to_thread(self.store.notify_work, run_id)
        return await asyncio.to_thread(self.store.get_runtime_run, run_id)

    async def wait(self, run_id: str, timeout: float | None = None) -> Any:
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
            await self.supervisor.wait(run_id, timeout=timeout)
        except (KeyError, asyncio.CancelledError, TimeoutError):
            pass
        returnable = {
            "waiting_input",
            "waiting_approval",
            "waiting_external",
            "scheduled",
            "paused",
            "completed",
            "failed",
            "cancelled",
            "timed_out",
        }
        delay = 0.05
        while True:
            record = await asyncio.to_thread(self.store.get_runtime_run, run_id)
            if record is None or record.status in returnable:
                return record
            if deadline is not None and time.monotonic() >= deadline:
                return record
            # Exponential backoff caps the poll rate for long-running runs.
            await asyncio.sleep(delay)
            delay = min(1.0, delay * 2)
