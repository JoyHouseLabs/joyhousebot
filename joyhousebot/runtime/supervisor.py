"""Bounded lifecycle management for native agent runs."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from joyhousebot.runtime.context import CancellationToken

RunFactory = Callable[[CancellationToken], Awaitable[Any]]


@dataclass(slots=True)
class RunHandle:
    run_id: str
    task: asyncio.Task[Any]
    cancellation: CancellationToken


class TaskSupervisor:
    """Own every background runtime task and make cancellation observable."""

    def __init__(self, max_concurrent: int | None = None, *, max_completed: int = 1024) -> None:
        self._max_concurrent = max_concurrent if isinstance(max_concurrent, int) and max_concurrent > 0 else None
        self._semaphore = (
            asyncio.Semaphore(self._max_concurrent)
            if self._max_concurrent is not None
            else None
        )
        self._handles: dict[str, RunHandle] = {}
        self._completed: OrderedDict[str, asyncio.Future[Any]] = OrderedDict()
        self._max_completed = max(1, max_completed)
        self._lock = asyncio.Lock()
        self._closing = False
        self._active_count = 0

    def capacity_snapshot(self, *, fallback_slots: int) -> dict[str, int]:
        """Return event-loop-local execution capacity for a Worker heartbeat."""
        slots = self._max_concurrent or fallback_slots
        submitted = len(self._handles)
        return {
            "slots": max(1, int(slots)),
            "active": self._active_count,
            "waiting": max(0, submitted - self._active_count),
        }

    async def submit(self, run_id: str, factory: RunFactory) -> RunHandle:
        async with self._lock:
            existing = self._handles.get(run_id)
            if existing is not None:
                return existing
            if self._closing:
                raise RuntimeError("task supervisor is closing")
            cancellation = CancellationToken()
            completion = asyncio.get_running_loop().create_future()
            completion.add_done_callback(
                lambda future: None if future.cancelled() else future.exception()
            )
            self._completed[run_id] = completion
            while len(self._completed) > self._max_completed:
                oldest_id, oldest = next(iter(self._completed.items()))
                if not oldest.done():
                    break
                self._completed.pop(oldest_id, None)

            async def _execute() -> Any:
                try:
                    if self._semaphore is None:
                        self._active_count += 1
                        try:
                            result = await factory(cancellation)
                        finally:
                            self._active_count -= 1
                    else:
                        async with self._semaphore:
                            self._active_count += 1
                            try:
                                cancellation.raise_if_cancelled()
                                result = await factory(cancellation)
                            finally:
                                self._active_count -= 1
                    if not completion.done():
                        completion.set_result(result)
                    return result
                except asyncio.CancelledError:
                    cancellation.cancel(cancellation.reason or "cancelled")
                    if not completion.done():
                        completion.cancel()
                    raise
                except BaseException as exc:
                    if not completion.done():
                        completion.set_exception(exc)
                    raise
                finally:
                    async with self._lock:
                        self._handles.pop(run_id, None)

            task = asyncio.create_task(_execute(), name=f"agent-run:{run_id}")
            # Submitted runs are intentionally detached from the request
            # coroutine. Consume terminal exceptions here so a provider/tool
            # failure is represented by the durable Run record, not an
            # unhandled "Task exception was never retrieved" warning.
            task.add_done_callback(
                lambda finished: None
                if finished.cancelled()
                else finished.exception()
            )
            handle = RunHandle(run_id=run_id, task=task, cancellation=cancellation)
            self._handles[run_id] = handle
            return handle

    async def cancel(self, run_id: str, reason: str = "cancelled by user") -> bool:
        async with self._lock:
            handle = self._handles.get(run_id)
            if handle is None:
                return False
            handle.cancellation.cancel(reason)
            handle.task.cancel()
            return True

    async def wait(self, run_id: str, timeout: float | None = None) -> Any:
        async with self._lock:
            future = self._completed.get(run_id)
        if future is None:
            raise KeyError(run_id)
        waiter = asyncio.shield(future)
        try:
            if timeout is None:
                return await waiter
            return await asyncio.wait_for(waiter, timeout=max(0.0, timeout))
        finally:
            if future.done():
                async with self._lock:
                    if self._completed.get(run_id) is future:
                        self._completed.pop(run_id, None)

    async def is_active(self, run_id: str) -> bool:
        async with self._lock:
            return run_id in self._handles

    async def active_run_ids(self) -> list[str]:
        async with self._lock:
            return list(self._handles)

    async def close(self) -> None:
        async with self._lock:
            self._closing = True
            handles = list(self._handles.values())
        for handle in handles:
            handle.cancellation.cancel("runtime shutting down")
            handle.task.cancel()
        if handles:
            await asyncio.gather(*(handle.task for handle in handles), return_exceptions=True)
