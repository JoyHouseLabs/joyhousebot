"""Promote an existing planning Run into a durable distributed task graph."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from joyhousebot.orchestration.task_graph import graph_task_id, validate_and_order_graph
from joyhousebot.runtime.models import AgentEvent, EventType, TaskGraphSpec


class GraphMaterializationMixin:
    async def materialize_graph(
        self,
        run_id: str,
        spec: TaskGraphSpec,
        *,
        worker_id: str | None = None,
        lease_version: int | None = None,
    ) -> Any:
        ordered = validate_and_order_graph(spec.tasks)
        record = await asyncio.to_thread(
            self.store.get_runtime_run, run_id, expected_user_id=spec.user_id
        )
        if record is None:
            raise ValueError("planning run not found")
        options = {
            **dict(record.options or {}),
            "goal": spec.goal,
            "max_concurrent": max(1, spec.max_concurrent),
            "fail_fast": spec.fail_fast,
            "aggregate": spec.aggregate,
            "aggregation_policy": dict(spec.aggregation_policy),
            "metadata": {
                **dict((record.options or {}).get("metadata") or {}),
                **dict(spec.metadata),
            },
            "tasks": [asdict(task) for task in ordered],
        }
        rows = [
            {
                "task_id": graph_task_id(run_id, task.id),
                "agent_id": task.agent_id or spec.agent_id,
                "name": task.name or task.id,
                "payload": {
                    "spec_id": task.id,
                    "agent_id": task.agent_id or spec.agent_id,
                    "prompt": task.prompt,
                    "metadata": task.metadata,
                    "timeout_seconds": task.timeout_seconds,
                    "capability": task.capability.to_dict() if task.capability else None,
                    "capability_input": task.capability_input,
                    "output_schema": task.output_schema,
                    "allowed_tools": task.allowed_tools,
                    "skill_names": task.skill_names,
                },
                "dependencies": [
                    graph_task_id(run_id, dependency) for dependency in task.dependencies
                ],
                "priority": index,
                "max_attempts": task.max_attempts,
            }
            for index, task in enumerate(ordered)
        ]
        materialized = await asyncio.to_thread(
            self.store.materialize_runtime_graph,
            run_id=run_id,
            user_id=spec.user_id,
            options=options,
            tasks=rows,
            worker_id=worker_id,
            lease_version=lease_version,
        )
        await self.events.publish(
            AgentEvent(
                run_id=run_id,
                type=EventType.PLAN_UPDATED.value,
                phase="planning",
                status="completed",
                data={"kind": "graph", "task_count": len(rows)},
            )
        )
        for task in ordered:
            await self.events.publish(
                AgentEvent(
                    run_id=run_id,
                    task_id=graph_task_id(run_id, task.id),
                    type=EventType.TASK_QUEUED.value,
                    data={"name": task.name or task.id, "dependencies": task.dependencies},
                )
            )
        await asyncio.to_thread(self.store.notify_work, run_id)
        return materialized
