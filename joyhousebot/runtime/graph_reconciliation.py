"""Shared post-Task Graph reconciliation, including automatic Saga handling."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.runtime.graph_saga_execution import reconcile_graph_saga, saga_mode

_ACTIVE_TASKS = ("queued", "blocked", "running", "waiting_approval", "waiting_external")


async def reconcile_after_graph_task(
    runtime: Any, run: Any, *, task: Any, suspended: bool
) -> None:
    counts = await asyncio.to_thread(
        runtime.stores.graphs.reconcile_runtime_graph, run.run_id
    )
    if saga_mode(run):
        await reconcile_graph_saga(runtime, run)
        counts = await asyncio.to_thread(
            runtime.stores.graphs.reconcile_runtime_graph, run.run_id
        )
    else:
        deferred_loop_failure = bool(task.payload.get("bounded_loop_parent_task_id"))
        if bool(run.options.get("fail_fast")) and counts.get("failed", 0) and not deferred_loop_failure:
            await asyncio.to_thread(
                runtime.stores.tasks.cancel_runtime_tasks, run.run_id
            )
            counts = await asyncio.to_thread(
                runtime.stores.graphs.reconcile_runtime_graph, run.run_id
            )
    await runtime._publish_graph_progress(run.run_id, counts=counts)
    await runtime._log(
        run.run_id,
        "graph.reconciled",
        "Graph dependency state reconciled",
        data={"counts": counts, "suspended": suspended},
    )
    if not any(counts.get(status, 0) for status in _ACTIVE_TASKS):
        await runtime._try_finalize_graph(run.run_id)
