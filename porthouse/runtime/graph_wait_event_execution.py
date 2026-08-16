"""Worker-side suspension of durable Graph ``wait_event`` nodes."""

from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from typing import Any

from porthouse.runtime.models import AgentEvent, EventType


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return sha256(encoded).hexdigest()


async def execute_graph_wait_event(runtime: Any, run: Any, task: Any) -> None:
    configuration = dict(task.payload["wait_event"])
    config_hash = _hash(
        {
            "graph_revision_id": task.payload.get("graph_revision_id"),
            "task_id": task.task_id,
            "configuration": configuration,
        }
    )
    wait_id = f"eventwait_{_hash(f'{task.task_id}:{task.lease_version}:{config_hash}')}"
    record = await asyncio.to_thread(
        runtime.store.suspend_graph_task_for_event,
        run_id=run.run_id,
        task_id=task.task_id,
        wait_id=wait_id,
        event_type=str(configuration["event_type"]),
        payload_schema=dict(configuration["payload_schema"]),
        deadline_seconds=int(configuration["deadline_seconds"]),
        config_hash=config_hash,
        worker_id=runtime.worker_id,
        lease_version=task.lease_version,
    )
    if record is None:
        raise asyncio.CancelledError("event wait suspension fenced by a newer lease")
    data = {
        "wait_id": record.wait_id,
        "event_type": record.event_type,
        "deadline_at": record.deadline_at,
        "token_issued": record.token_issued,
    }
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.EVENT_WAITING.value,
            status="waiting_external",
            data=data,
        )
    )
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.RUN_WAITING_EXTERNAL.value,
            status="waiting_external",
            data={**data, "waiting_on": record.wait_id},
        )
    )
    await runtime._log(
        run.run_id,
        "graph.event.waiting",
        "Graph Task is waiting for an authenticated external event",
        task_id=task.task_id,
        data=data,
    )
