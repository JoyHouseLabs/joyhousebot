"""RuntimeControls for the durable Agent runtime."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from porthouse.runtime.models import (
    AgentEvent,
    EventType,
    RunStatus,
)


def _lease_alive(record: Any) -> bool:
    """Whether the run's lease is still held by a live owning worker."""
    if record.status != RunStatus.RUNNING.value or not record.lease_owner:
        return False
    if not record.lease_expires_at:
        return False
    try:
        expires = datetime.fromisoformat(record.lease_expires_at)
    except ValueError:
        return False
    return expires.timestamp() > time.time()


class RuntimeControlsMixin:
    async def cancel(self, run_id: str, reason: str = "cancelled by user") -> bool:
        record = await asyncio.to_thread(self.store.get_runtime_run, run_id)
        if record is None or record.status in {"completed", "failed", "cancelled", "timed_out"}:
            return False
        # Durable intent first (phase one): even if this process dies, the
        # owning worker or the recovery sweep finishes the terminal state.
        request = await asyncio.to_thread(self.store.request_runtime_cancel, run_id, reason=reason)
        if request is None:
            return False
        active = await self.supervisor.cancel(run_id, reason)
        if active:
            # The in-process owner aborts through its cancellation token and
            # commits the fenced terminal cancelled state itself.
            return True
        if request["status"] == "running" and request["lease_alive"]:
            # Owned by another live worker: it observes the request on its
            # next heartbeat and commits the terminal state with fencing.
            await self.events.publish(
                AgentEvent(
                    run_id=run_id,
                    type=EventType.RUN_CANCELLING.value,
                    data={"reason": reason},
                )
            )
            return True
        # No live lease (queued/waiting, or the owning worker is gone): a
        # non-owner terminal transition is permitted only in this case.
        await asyncio.to_thread(self.store.cancel_runtime_tasks, run_id)
        await self._finish_error(
            run_id,
            RunStatus.CANCELLED,
            EventType.RUN_CANCELLED,
            reason,
            record.started_at or record.created_at,
        )
        return True

    async def _finish_cancel_requested_run(self, record: Any) -> None:
        """Recovery sweep: finish a cancel-requested run whose lease is dead.

        A live owner observes the request on its heartbeat and commits the
        terminal state itself.  Once the lease is dead, finish fencing
        permits a non-owner transition, so the sweep completes the cancel.
        """
        if _lease_alive(record):
            return
        await asyncio.to_thread(self.store.cancel_runtime_tasks, record.run_id)
        await self._finish_error(
            record.run_id,
            RunStatus.CANCELLED,
            EventType.RUN_CANCELLED,
            record.cancel_reason or "cancelled by user",
            record.started_at or record.created_at,
        )

    async def resume(self, run_id: str) -> Any:
        current = await asyncio.to_thread(self.store.get_runtime_run, run_id)
        if current is not None and current.kind == "graph":
            get_saga = getattr(self.store, "get_runtime_saga", None)
            saga = await asyncio.to_thread(get_saga, run_id) if get_saga else None
            if saga is not None:
                raise ValueError(
                    "Saga Graph runs cannot be resumed; submit a new Run after compensation"
                )
        reset = await asyncio.to_thread(
            self.store.reset_runtime_graph
            if current is not None and current.kind == "graph"
            else self.store.reset_runtime_run,
            run_id,
        )
        if not reset:
            raise ValueError("only failed, cancelled, or timed out runs can be resumed")
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
