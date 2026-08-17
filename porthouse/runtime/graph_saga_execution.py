"""Runtime event projection for the durable Graph Saga state machine."""

from __future__ import annotations

import asyncio
from typing import Any

from porthouse.runtime.models import AgentEvent, EventType


def saga_mode(run: Any) -> bool:
    return dict(run.options.get("failure_policy") or {}).get("mode") == "saga"


async def reconcile_graph_saga(runtime: Any, run: Any) -> dict[str, Any] | None:
    if not saga_mode(run):
        return None
    state = await asyncio.to_thread(
        runtime.stores.graphs.trigger_runtime_saga, run.run_id
    )
    if state is None:
        return None
    state = await asyncio.to_thread(
        runtime.stores.graphs.reconcile_runtime_saga, run.run_id
    )
    assert state is not None
    common = {
        "saga_id": state["saga_id"],
        "trigger_task_id": state["trigger_task_id"],
        "trigger_status": state["trigger_status"],
        "compensation_total": state["compensation_total"],
        "compensation_completed": state["compensation_completed"],
    }
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=state["trigger_task_id"],
            type=EventType.SAGA_STARTED.value,
            event_id=f"{state['saga_id']}:started",
            status="running",
            data=common,
        )
    )
    tasks = await asyncio.to_thread(
        runtime.stores.tasks.list_runtime_tasks, run_id=run.run_id, limit=5000
    )
    for task in tasks:
        if str(task.payload.get("saga_id") or "") != state["saga_id"]:
            continue
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=EventType.TASK_QUEUED.value,
                event_id=f"{task.task_id}:saga.queued",
                status="queued",
                data={
                    "reason": "saga_compensation",
                    "saga_id": state["saga_id"],
                    "saga_order": task.payload.get("saga_order"),
                },
            )
        )
    if state["status"] in {"completed", "failed"}:
        completed = state["status"] == "completed"
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=state["trigger_task_id"],
                type=(
                    EventType.SAGA_COMPLETED.value if completed else EventType.SAGA_FAILED.value
                ),
                event_id=f"{state['saga_id']}:{state['status']}",
                status=state["status"],
                data={**common, "error": state["error"]},
            )
        )
    return state
