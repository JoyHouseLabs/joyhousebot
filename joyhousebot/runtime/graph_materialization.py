"""Promote an existing planning Run into a durable distributed task graph."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.orchestration.failure_policy import validate_saga_declarations
from joyhousebot.orchestration.task_graph import graph_task_id, validate_and_order_graph
from joyhousebot.runtime.graph_revision import (
    freeze_graph_revision,
    graph_options,
    graph_task_rows,
)
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
        catalog = await asyncio.to_thread(
            self.stores.catalog.list_capability_definitions
        )
        validate_saga_declarations(
            ordered,
            catalog,
            spec.failure_policy,
            max_concurrent=spec.max_concurrent,
        )
        record = await asyncio.to_thread(
            self.stores.runs.get_runtime_run,
            run_id,
            expected_user_id=spec.user_id,
        )
        if record is None:
            raise ValueError("planning run not found")
        # Planning is allowed to replace execution structure, but not to drop
        # the immutable inputs frozen on the accepted top-level Run.
        if not spec.input_asset_ids:
            spec.input_asset_ids = list((record.options or {}).get("input_asset_ids") or [])
        revision = freeze_graph_revision(
            run_id, spec, ordered, source="planning_materialization"
        )
        options = graph_options(dict(record.options or {}), spec, revision)
        rows = graph_task_rows(run_id, revision)
        materialized = await asyncio.to_thread(
            self.stores.graphs.materialize_runtime_graph,
            run_id=run_id,
            user_id=spec.user_id,
            options=options,
            tasks=rows,
            revision=revision,
            created_by=f"runtime:{worker_id or 'coordinator'}",
            worker_id=worker_id,
            lease_version=lease_version,
        )
        await self.events.publish(
            AgentEvent(
                run_id=run_id,
                type=EventType.PLAN_UPDATED.value,
                phase="planning",
                status="completed",
                data={
                    "kind": "graph",
                    "task_count": len(rows),
                    "graph_revision_id": revision["revision_id"],
                },
            )
        )
        for task in ordered:
            if (
                dict(revision["settings"].get("failure_policy") or {}).get("mode")
                == "saga"
                and task.node_type == "compensation"
            ):
                continue
            await self.events.publish(
                AgentEvent(
                    run_id=run_id,
                    task_id=graph_task_id(run_id, task.id),
                    type=EventType.TASK_QUEUED.value,
                    data={"name": task.name or task.id, "dependencies": task.dependencies},
                )
            )
        await asyncio.to_thread(self.stores.workers.notify_work, run_id)
        return materialized
