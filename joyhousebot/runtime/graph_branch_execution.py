"""Dependency context and deterministic branch execution for Graph Tasks."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from joyhousebot.orchestration.branching import branch_targets, evaluate_branch
from joyhousebot.runtime.models import AgentEvent, EventType


def capability_result_prompt(result: Any) -> str:
    content = result.data.get("content")
    return str(content) if content is not None else str(result.data)


def dependency_result_variables(
    dependency_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        f"tasks.{key}.{field}": value.get(field)
        for key, value in dependency_results.items()
        for field in ("content", "structured_output", "capability_result")
    }


async def graph_task_prompt(runtime: Any, task: Any) -> tuple[str, dict[str, dict[str, Any]]]:
    dependencies = await asyncio.to_thread(
        runtime.store.get_runtime_task_dependencies, task.task_id
    )
    context: dict[str, dict[str, Any]] = {}
    for dependency_id in dependencies:
        dependency = await asyncio.to_thread(runtime.store.get_runtime_task, dependency_id)
        if dependency is not None:
            key = str(dependency.payload.get("spec_id") or dependency.task_id)
            context[key] = dict(dependency.result or {})
    prompt = str(task.payload.get("prompt") or "")
    if context:
        content_context = {key: value.get("content") for key, value in context.items()}
        prompt += (
            "\n\nContext from dependency tasks:\n"
            + json.dumps(content_context, ensure_ascii=False)[:20000]
        )
    return prompt, context


async def execute_graph_branch(
    runtime: Any,
    run: Any,
    task: Any,
    dependency_results: dict[str, dict[str, Any]],
) -> None:
    configuration = dict(task.payload.get("branch") or {})
    decision = evaluate_branch(configuration, dependency_results)
    frozen_targets = branch_targets(configuration)
    selected = set(decision.selected_targets)
    value = {
        "status": "completed",
        "node_type": "branch",
        "selected_targets": sorted(selected),
        "matched_case": decision.matched_case,
        "used_default": decision.used_default,
        "source": decision.source,
        "operator": decision.operator,
    }
    value["structured_output"] = {
        "selected_targets": value["selected_targets"],
        "matched_case": value["matched_case"],
        "used_default": value["used_default"],
    }
    value["content"] = json.dumps(value["structured_output"], ensure_ascii=False, sort_keys=True)
    saved, skipped_task_ids = await asyncio.to_thread(
        runtime.store.complete_runtime_branch,
        run_id=run.run_id,
        task_id=task.task_id,
        selected_target_ids=[f"{run.run_id}:{item}" for item in sorted(selected)],
        all_target_ids=[f"{run.run_id}:{item}" for item in sorted(frozen_targets)],
        result=value,
        worker_id=runtime.worker_id,
        lease_version=task.lease_version,
    )
    if not saved:
        raise asyncio.CancelledError("branch completion fenced by a newer lease")
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.BRANCH_EVALUATED.value,
            status="completed",
            data=value,
        )
    )
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.TASK_COMPLETED.value,
            status="completed",
            data=value,
        )
    )
    for skipped_task_id in skipped_task_ids:
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=skipped_task_id,
                type=EventType.TASK_SKIPPED.value,
                status="skipped",
                data={
                    "reason": "branch_not_selected",
                    "branch_task_id": task.task_id,
                },
            )
        )
    await runtime._log(
        run.run_id,
        "graph.branch.evaluated",
        "Graph branch selected frozen targets",
        task_id=task.task_id,
        data={**value, "skipped_task_ids": skipped_task_ids},
    )
