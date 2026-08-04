"""Process-local fan-out for PostgreSQL durable-work notifications.

PostgreSQL ``NOTIFY`` is deliberately only a wake-up hint: durable rows and
``SKIP LOCKED`` remain the source of truth.  One listener per runtime process
converts those hints into a generation-based condition so the Run and Task
dispatchers wake together without competing for the same listener connection.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkWake:
    generation: int
    source: str  # pg_notify | poll | recovery | local


class RuntimeWorkSignal:
    """One store listener plus in-process broadcast for one runtime process."""

    def __init__(self, store: Any, *, fallback_poll_seconds: float) -> None:
        self._store = store
        self._fallback_poll_seconds = max(0.1, min(float(fallback_poll_seconds), 5.0))
        self._condition = asyncio.Condition()
        self._generation = 1
        self._source = "recovery"
        self._closing = False
        self._listener_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._listener_task is None:
            self._listener_task = asyncio.create_task(
                self._listen(), name="runtime-work-listener"
            )

    async def close(self) -> None:
        self._closing = True
        if self._listener_task is not None:
            self._listener_task.cancel()
            await asyncio.gather(self._listener_task, return_exceptions=True)
            self._listener_task = None
        await self.signal("local")

    async def signal(self, source: str) -> None:
        async with self._condition:
            self._generation += 1
            self._source = source
            self._condition.notify_all()

    async def wait(self, after_generation: int) -> WorkWake:
        async with self._condition:
            while not self._closing and self._generation == after_generation:
                await self._condition.wait()
            return WorkWake(self._generation, self._source)

    async def _listen(self) -> None:
        waiter = getattr(self._store, "wait_for_work", None)
        while not self._closing:
            try:
                notified = (
                    await asyncio.to_thread(waiter, self._fallback_poll_seconds)
                    if callable(waiter)
                    else await self._fallback_sleep()
                )
                await self.signal("pg_notify" if notified else "poll")
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed listener is not a correctness failure.  Keep the
                # dispatcher alive through the configured durable polling path.
                await self.signal("poll")
                await asyncio.sleep(self._fallback_poll_seconds)

    async def _fallback_sleep(self) -> bool:
        await asyncio.sleep(self._fallback_poll_seconds)
        return False
