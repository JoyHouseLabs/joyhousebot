"""Worker-side materialization of bounded Graph ``foreach`` instances."""

from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from typing import Any

from porthouse.orchestration.foreach import select_foreach_items
from porthouse.orchestration.task_graph import render_value
from porthouse.runtime.models import AgentEvent, EventType


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return sha256(encoded).hexdigest()


def _prompt(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _item_variables(item: Any, index: int) -> dict[str, Any]:
    variables = {"item": item}
    if not isinstance(item, (dict, list)):
        variables["item.value"] = item
    pending = [("item", item, 0)]
    while pending and len(variables) < 256:
        prefix, value, depth = pending.pop()
        if depth >= 8:
            continue
        entries = (
            value.items()
            if isinstance(value, dict)
            else enumerate(value)
            if isinstance(value, list)
            else []
        )
        for key, child in entries:
            child_key = f"{prefix}.{key}"
            variables[child_key] = child
            if isinstance(child, (dict, list)):
                pending.append((child_key, child, depth + 1))
    variables["item.index"] = index
    return variables


def _foreach_children(task: Any, items: list[Any]) -> tuple[str, list[dict[str, Any]]]:
    configuration = dict(task.payload["foreach"])
    template = dict(configuration["template"])
    template_type = str(
        template.get("node_type") or ("capability" if template.get("capability") else "agent")
    )
    item_hashes = [_hash(item) for item in items]
    expansion_id = _hash(
        {
            "graph_revision_id": task.payload.get("graph_revision_id"),
            "task_id": task.task_id,
            "source": configuration["source"],
            "path": configuration["path"],
            "template": template,
            "item_hashes": item_hashes,
        }
    )
    rows = []
    for index, (item, item_hash) in enumerate(zip(items, item_hashes, strict=True)):
        variables = _item_variables(item, index)
        child_id = f"{task.task_id}:item:{index:04d}:{item_hash[:12]}"
        rows.append(
            {
                "task_id": child_id,
                "agent_id": str(template.get("agent_id") or task.agent_id),
                "name": str(template.get("name") or f"{task.name} [{index + 1}]"),
                "payload": {
                    "spec_id": f"{task.payload.get('spec_id')}[{index}]",
                    "graph_revision_id": task.payload.get("graph_revision_id"),
                    "node_type": template_type,
                    "agent_id": str(template.get("agent_id") or task.agent_id),
                    "prompt": _prompt(render_value(template.get("prompt") or "", variables)),
                    "metadata": {
                        **dict(render_value(template.get("metadata") or {}, variables)),
                        "foreach_parent_task_id": task.task_id,
                        "foreach_item_index": index,
                        "foreach_item_hash": item_hash,
                    },
                    "timeout_seconds": float(template.get("timeout_seconds") or 300),
                    "capability": template.get("capability"),
                    "capability_input": render_value(
                        template.get("capability_input") or {}, variables
                    ),
                    "output_schema": template.get("output_schema"),
                    "verification_policy": template.get("verification_policy") or {},
                    "max_repairs": template.get("max_repairs"),
                    "allowed_tools": list(template.get("allowed_tools") or []),
                    "skill_names": list(template.get("skill_names") or []),
                    "branch": {},
                    "foreach": {},
                    "wait_event": {},
                    "foreach_item": item,
                    "foreach_item_index": index,
                    "foreach_item_hash": item_hash,
                    "foreach_parent_task_id": task.task_id,
                    "foreach_max_concurrent": int(configuration["max_concurrent"]),
                },
                "priority": task.priority + index + 1,
                "max_attempts": int(template.get("max_attempts") or 1),
            }
        )
    return expansion_id, rows


async def execute_graph_foreach(
    runtime: Any,
    run: Any,
    task: Any,
    dependency_results: dict[str, dict[str, Any]],
) -> None:
    previous = dict(task.result or {})
    if previous.get("stop_reason") == "foreach_expanded":
        result = await asyncio.to_thread(
            runtime.store.complete_runtime_foreach,
            run_id=run.run_id,
            task_id=task.task_id,
            worker_id=runtime.worker_id,
            lease_version=task.lease_version,
        )
        if result is None:
            raise asyncio.CancelledError("foreach completion fenced by a newer lease")
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=EventType.FOREACH_COMPLETED.value,
                status="completed",
                data={
                    "expansion_id": result["expansion_id"],
                    "item_count": result["item_count"],
                },
            )
        )
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=EventType.TASK_COMPLETED.value,
                status="completed",
                data=result,
            )
        )
        return
    items = select_foreach_items(dict(task.payload["foreach"]), dependency_results)
    expansion_id, children = _foreach_children(task, items)
    outcome = await asyncio.to_thread(
        runtime.store.expand_runtime_foreach,
        run_id=run.run_id,
        task_id=task.task_id,
        expansion_id=expansion_id,
        children=children,
        worker_id=runtime.worker_id,
        lease_version=task.lease_version,
    )
    if not outcome["saved"]:
        raise asyncio.CancelledError("foreach expansion fenced by a newer lease")
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.FOREACH_EXPANDED.value,
            status=outcome["status"],
            data={
                "expansion_id": expansion_id,
                "item_count": len(children),
                "child_task_ids": outcome["child_task_ids"],
            },
        )
    )
    for child in children:
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=child["task_id"],
                parent_task_id=task.task_id,
                type=EventType.TASK_QUEUED.value,
                status="queued",
                data={
                    "reason": "foreach_item",
                    "foreach_parent_task_id": task.task_id,
                    "foreach_item_index": child["payload"]["foreach_item_index"],
                },
            )
        )
    if outcome["status"] == "completed":
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=EventType.FOREACH_COMPLETED.value,
                status="completed",
                data={"expansion_id": expansion_id, "item_count": 0},
            )
        )
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=EventType.TASK_COMPLETED.value,
                status="completed",
                data=outcome["result"],
            )
        )
