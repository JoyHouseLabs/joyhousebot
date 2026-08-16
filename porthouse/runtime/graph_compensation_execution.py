"""Explicit, auditable compensation-node execution helpers."""

from __future__ import annotations

import asyncio
from typing import Any

from porthouse.orchestration.control_nodes import control_source_id
from porthouse.runtime.models import AgentEvent, EventType


async def prepare_graph_compensation(runtime: Any, run: Any, task: Any) -> dict[str, Any]:
    source_id = control_source_id(dict(task.payload.get("compensation") or {}))
    source_task_id = f"{run.run_id}:{source_id}"
    source_task = await asyncio.to_thread(runtime.store.get_runtime_task, source_task_id)
    if source_task is None or source_task.status != "completed":
        raise RuntimeError("compensation source Task is not completed")
    actions = await asyncio.to_thread(runtime.store.list_action_intents, run.run_id)
    candidates = [item for item in actions if item.task_id == source_task_id]
    source_action = None
    for action in reversed(candidates):
        observation = await asyncio.to_thread(
            runtime.store.get_action_observation, action.action_id
        )
        if observation is not None and observation.status == "succeeded":
            source_action = action
            break
    if source_action is None:
        raise RuntimeError("compensation source has no confirmed successful Action")
    data = {
        "source_task_id": source_task_id,
        "source_action_id": source_action.action_id,
        "compensation_capability_id": str(
            (task.payload.get("capability") or {}).get("capability_id") or ""
        ),
        "saga_id": task.payload.get("saga_id"),
        "saga_order": task.payload.get("saga_order"),
    }
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.COMPENSATION_STARTED.value,
            event_id=f"{task.task_id}:compensation.started",
            status="running",
            data=data,
        )
    )
    await runtime._log(
        run.run_id,
        "graph.compensation.started",
        "Explicit compensation Action started",
        task_id=task.task_id,
        data=data,
    )
    return data


async def complete_graph_compensation(
    runtime: Any,
    run: Any,
    task: Any,
    context: dict[str, Any],
) -> dict[str, Any]:
    actions = await asyncio.to_thread(runtime.store.list_action_intents, run.run_id)
    candidates = [item for item in actions if item.task_id == task.task_id]
    compensation_action = candidates[-1] if candidates else None
    if compensation_action is None:
        raise RuntimeError("compensation Action ledger entry is missing")
    value = {
        **context,
        "node_type": "compensation",
        "compensation_action_id": compensation_action.action_id,
    }
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.COMPENSATION_COMPLETED.value,
            event_id=f"{task.task_id}:compensation.completed",
            status="completed",
            data=value,
        )
    )
    await runtime._log(
        run.run_id,
        "graph.compensation.completed",
        "Explicit compensation Action completed",
        task_id=task.task_id,
        data=value,
    )
    return value


async def publish_graph_compensation_failed(
    runtime: Any, run: Any, task: Any, error: Exception
) -> None:
    source_id = control_source_id(dict(task.payload.get("compensation") or {}))
    data = {
        "source_task_id": f"{run.run_id}:{source_id}",
        "error": str(error),
    }
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.COMPENSATION_FAILED.value,
            event_id=f"{task.task_id}:compensation.failed",
            status="failed",
            data=data,
        )
    )
