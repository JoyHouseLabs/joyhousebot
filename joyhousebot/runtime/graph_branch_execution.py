"""Dependency context and deterministic branch execution for Graph Tasks."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Protocol

from joyhousebot.orchestration.branching import branch_targets, evaluate_branch
from joyhousebot.runtime.models import AgentEvent, EventType
from joyhousebot.storage.contracts import RuntimeStores
from joyhousebot.storage.runtime_store import RuntimeTaskRecord

_VARIABLE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_NESTED_VARIABLE_DEPTH = 8
_MAX_NESTED_VARIABLES_PER_FIELD = 512


class GraphPromptRuntime(Protocol):
    stores: RuntimeStores


def capability_result_prompt(result: Any) -> str:
    content = result.data.get("content")
    return str(content) if content is not None else str(result.data)


def dependency_result_variables(
    dependency_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    variables: dict[str, Any] = {}

    def add_nested(
        prefix: str,
        value: Any,
        *,
        depth: int,
        budget: list[int],
    ) -> None:
        if depth >= _MAX_NESTED_VARIABLE_DEPTH or not isinstance(value, dict):
            return
        for raw_segment, nested in value.items():
            if budget[0] <= 0:
                return
            segment = str(raw_segment)
            if not _VARIABLE_SEGMENT.fullmatch(segment):
                continue
            nested_prefix = f"{prefix}.{segment}"
            variables[nested_prefix] = nested
            budget[0] -= 1
            add_nested(
                nested_prefix,
                nested,
                depth=depth + 1,
                budget=budget,
            )

    for key, result in dependency_results.items():
        for field in ("content", "structured_output", "capability_result"):
            prefix = f"tasks.{key}.{field}"
            value = result.get(field)
            # Preserve the original top-level variables even when a nested result is
            # unusually large. Nested aliases are a bounded convenience for exact
            # Graph templates, never an unbounded expression evaluator.
            variables[prefix] = value
            add_nested(
                prefix,
                value,
                depth=0,
                budget=[_MAX_NESTED_VARIABLES_PER_FIELD],
            )
    return variables


async def _dependency_context(
    runtime: GraphPromptRuntime, task: RuntimeTaskRecord
) -> dict[str, dict[str, Any]]:
    dependencies = await asyncio.to_thread(
        runtime.stores.tasks.get_runtime_task_dependencies, task.task_id
    )
    context: dict[str, dict[str, Any]] = {}
    for dependency_id in dependencies:
        dependency = await asyncio.to_thread(
            runtime.stores.tasks.get_runtime_task, dependency_id
        )
        if dependency is not None:
            key = str(dependency.payload.get("spec_id") or dependency.task_id)
            context[key] = {
                **dict(dependency.result or {}),
                "artifact_id": f"{dependency.task_id}:output",
            }
    return context


def _dependency_prompt(
    context: dict[str, dict[str, Any]], *, structured: bool
) -> str:
    if not context:
        return ""
    if structured:
        content_context = {
            key: {
                field: value.get(field)
                for field in ("content", "structured_output", "artifact_id", "tools_used")
                if value.get(field) is not None
            }
            for key, value in context.items()
        }
    else:
        content_context = {key: value.get("content") for key, value in context.items()}
    return "\n\nContext from dependency tasks:\n" + json.dumps(
        content_context, ensure_ascii=False
    )[:20000]


def _frozen_team_prompt(metadata: dict[str, Any]) -> str:
    team_ref = dict(metadata["team_ref"])
    policy = dict(metadata.get("team_context_policy") or {})
    member = dict(metadata.get("team_member") or {})
    payload = {
        "team": team_ref,
        "member": {
            key: member.get(key)
            for key in (
                "member_id",
                "role",
                "responsibility",
                "can_delegate",
                "allowed_handoffs",
            )
        },
        "confirmed_inputs": dict(metadata.get("team_confirmed_inputs") or {}),
        "required_context": list(policy.get("required_context") or []),
        "excluded_context": list(policy.get("excluded_context") or []),
        "policies": {
            "context": policy,
            "budget": dict(metadata.get("team_budget_policy") or {}),
            "approval": dict(metadata.get("team_approval_policy") or {}),
        },
    }
    return "\n\nFrozen AgentTeam context:\n" + json.dumps(payload, ensure_ascii=False)


def _workspace_entry(
    item: dict[str, Any], *, fields: set[str], max_entry_chars: int
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "member_id": item["member_id"],
        "entry_type": item["entry_type"],
        "created_at": item["created_at"],
    }
    if "summary" in fields:
        value["summary"] = item["summary"]
    data = {
        key: item["data"].get(key)
        for key in ("content", "structured_output", "artifact_id", "tools_used", "usage")
        if key in fields and item["data"].get(key) is not None
    }
    if "content" in data:
        data["content"] = str(data["content"])[:max_entry_chars]
    if "structured_output" in data:
        encoded = json.dumps(data["structured_output"], ensure_ascii=False, default=str)
        if len(encoded) > max_entry_chars:
            data["structured_output"] = {
                "truncated": True,
                "preview": encoded[:max_entry_chars],
            }
    value["data"] = data
    return value


def _serialize_workspace(entries: list[dict[str, Any]], *, max_chars: int) -> str:
    serialized = json.dumps(entries, ensure_ascii=False)
    while len(serialized) > max_chars and len(entries) > 1:
        entries.pop(0)
        serialized = json.dumps(entries, ensure_ascii=False)
    if len(serialized) <= max_chars or not entries:
        return serialized
    data = entries[0]["data"]
    data["content"] = str(data.get("content") or "")[: max(200, max_chars // 2)]
    data["truncated"] = True
    return json.dumps(entries, ensure_ascii=False)


async def _workspace_prompt(
    runtime: GraphPromptRuntime,
    task: RuntimeTaskRecord,
    metadata: dict[str, Any],
    *,
    member_id: str,
) -> str:
    policy = dict(metadata.get("team_context_policy") or {})
    if not bool(policy.get("workspace_enabled", True)):
        return ""
    team_ref = dict(metadata["team_ref"])
    run = await asyncio.to_thread(runtime.stores.runs.get_runtime_run, task.run_id)
    if run is None:
        raise ValueError("AgentTeam Workspace parent Run is unavailable")
    entries = await asyncio.to_thread(
        runtime.stores.team_workspace.list_team_workspace_entries,
        user_id=run.user_id,
        root_run_id=str(metadata.get("team_workspace_run_id") or run.run_id),
        reader_member_id=member_id,
        coordinator=member_id == str(team_ref.get("coordinator_member_id") or ""),
        limit=max(1, min(int(policy.get("max_entries") or 20), 200)),
    )
    entry_types = set(policy.get("workspace_entry_types") or ("task_result", "subagent_result"))
    entries = [item for item in entries if item["entry_type"] in entry_types]
    if not entries:
        return ""
    fields = set(
        policy.get("workspace_fields")
        or ("summary", "content", "structured_output", "artifact_id")
    )
    max_entry_chars = max(500, min(int(policy.get("max_entry_chars") or 6000), 100000))
    shared = [
        _workspace_entry(item, fields=fields, max_entry_chars=max_entry_chars)
        for item in entries
    ]
    max_chars = max(1000, min(int(policy.get("max_chars") or 20000), 100000))
    return "\n\nShared AgentTeam Workspace:\n" + _serialize_workspace(
        shared, max_chars=max_chars
    )


async def graph_task_prompt(
    runtime: GraphPromptRuntime, task: RuntimeTaskRecord
) -> tuple[str, dict[str, dict[str, Any]]]:
    context = await _dependency_context(runtime, task)
    prompt = str(task.payload.get("prompt") or "")
    metadata = dict(task.payload.get("metadata") or {})
    team_ref = metadata.get("team_ref")
    prompt += _dependency_prompt(context, structured=isinstance(team_ref, dict))
    member_id = str(metadata.get("team_member_id") or "")
    if isinstance(team_ref, dict) and member_id:
        metadata["team_ref"] = team_ref
        prompt += _frozen_team_prompt(metadata)
        prompt += await _workspace_prompt(
            runtime,
            task,
            metadata,
            member_id=member_id,
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
        runtime.stores.graphs.complete_runtime_branch,
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
