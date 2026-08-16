"""Lease heartbeat and claim telemetry for Graph Task execution."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from porthouse.runtime.context import CancellationToken
from porthouse.runtime.models import AgentEvent, EventType


def _timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


async def graph_task_heartbeat(
    runtime: Any,
    run: Any,
    task: Any,
    cancellation: CancellationToken,
    owner_task: asyncio.Task[Any] | None,
) -> None:
    while True:
        await asyncio.sleep(max(1.0, runtime.lease_seconds / 3))
        owned = await asyncio.to_thread(
            runtime.store.heartbeat_runtime_task,
            task.task_id,
            worker_id=runtime.worker_id,
            lease_seconds=runtime.lease_seconds,
            lease_version=task.lease_version,
        )
        if owned:
            continue
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=EventType.LEASE_LOST.value,
                worker_id=runtime.worker_id,
                lease_version=task.lease_version,
                data={"reason": "task lease ownership lost"},
            )
        )
        cancellation.cancel("task ownership lost")
        if owner_task is not None:
            owner_task.cancel()
        return


async def publish_task_started(runtime: Any, task: Any, run: Any) -> None:
    await runtime._log(
        run.run_id,
        "task.claimed",
        "Graph task claimed",
        task_id=task.task_id,
        data={"attempt": task.attempt, "lease_version": task.lease_version},
    )
    details = runtime._task_claim_details.pop(task.task_id, {})
    available_at = _timestamp(task.available_at) or _timestamp(task.created_at)
    if available_at is not None:
        details["queue_wait_ms"] = max(0, int((time.time() - available_at) * 1000))
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.TASK_STARTED.value,
            data={
                "attempt": task.attempt,
                "name": task.name,
                "worker_id": runtime.worker_id,
                **details,
            },
        )
    )
