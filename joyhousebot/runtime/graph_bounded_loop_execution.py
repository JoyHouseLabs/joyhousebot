"""Worker execution for durable, bounded Graph loops."""

from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from types import SimpleNamespace
from typing import Any

from joyhousebot.orchestration.bounded_loop import (
    loop_should_exit,
    select_initial_loop_state,
    select_next_loop_state,
)
from joyhousebot.orchestration.task_graph import render_value
from joyhousebot.runtime.models import AgentEvent, AgentUsage, EventType

_TERMINAL = {"completed", "failed", "cancelled", "timed_out", "skipped"}


def _encoded(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("bounded_loop state must be JSON serializable") from exc
    if len(encoded) > 65_536:
        raise ValueError("bounded_loop state exceeds 64 KiB")
    return encoded


def _hash(value: Any) -> str:
    return sha256(_encoded(value)).hexdigest()


def _prompt(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _state_variables(state: Any, iteration: int) -> dict[str, Any]:
    variables = {
        "state": state,
        "iteration.index": iteration - 1,
        "iteration.number": iteration,
    }
    if not isinstance(state, (dict, list)):
        variables["state.value"] = state
    pending = [("state", state, 0)]
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
    return variables


def _loop_identity(task: Any, configuration: dict[str, Any], initial_state: Any) -> str:
    return _hash(
        {
            "graph_revision_id": task.payload.get("graph_revision_id"),
            "task_id": task.task_id,
            "configuration": configuration,
            "initial_state_hash": _hash(initial_state),
        }
    )


def _loop_child(
    task: Any,
    *,
    state: Any,
    iteration: int,
    loop_id: str,
) -> dict[str, Any]:
    configuration = dict(task.payload["bounded_loop"])
    template = dict(configuration["template"])
    template_type = str(
        template.get("node_type") or ("capability" if template.get("capability") else "agent")
    )
    state_hash = _hash(state)
    variables = _state_variables(state, iteration)
    child_id = f"{task.task_id}:loop:{iteration:03d}:{state_hash[:12]}"
    return {
        "task_id": child_id,
        "agent_id": str(template.get("agent_id") or task.agent_id),
        "name": str(template.get("name") or f"{task.name} iteration {iteration}"),
        "payload": {
            "spec_id": f"{task.payload.get('spec_id')}[{iteration}]",
            "graph_revision_id": task.payload.get("graph_revision_id"),
            "node_type": template_type,
            "agent_id": str(template.get("agent_id") or task.agent_id),
            "prompt": _prompt(render_value(template.get("prompt") or "", variables)),
            "metadata": {
                **dict(render_value(template.get("metadata") or {}, variables)),
                "bounded_loop_parent_task_id": task.task_id,
                "bounded_loop_iteration": iteration,
                "bounded_loop_id": loop_id,
                "bounded_loop_input_state_hash": state_hash,
            },
            "timeout_seconds": float(template.get("timeout_seconds") or 300),
            "capability": template.get("capability"),
            "capability_input": render_value(
                template.get("capability_input") or {}, variables
            ),
            "output_schema": template["output_schema"],
            "verification_policy": template.get("verification_policy") or {},
            "max_repairs": template.get("max_repairs"),
            "allowed_tools": list(template.get("allowed_tools") or []),
            "skill_names": list(template.get("skill_names") or []),
            "branch": {},
            "foreach": {},
            "wait_event": {},
            "approval": {},
            "verify": {},
            "compensation": {},
            "bounded_loop": {},
            "bounded_loop_parent_task_id": task.task_id,
            "bounded_loop_iteration": iteration,
            "bounded_loop_id": loop_id,
            "bounded_loop_input_state": state,
            "bounded_loop_input_state_hash": state_hash,
            "foreach_max_concurrent": 1,
        },
        "priority": task.priority + iteration,
        "max_attempts": int(template.get("max_attempts") or 1),
    }


def _usage(children: list[Any]) -> dict[str, Any]:
    usage = AgentUsage()
    for child in children:
        usage.add(AgentUsage.from_dict(dict(child.result or {}).get("usage")))
    return usage.to_dict()


async def _publish_iteration_ledger(runtime: Any, run: Any, parent: Any, children: list[Any]) -> None:
    for child in children:
        iteration = int(child.payload["bounded_loop_iteration"])
        common = {
            "loop_id": child.payload["bounded_loop_id"],
            "iteration": iteration,
            "child_task_id": child.task_id,
            "input_state_hash": child.payload["bounded_loop_input_state_hash"],
        }
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=parent.task_id,
                type=EventType.LOOP_ITERATION_STARTED.value,
                event_id=f"{child.task_id}:loop.iteration_started",
                status="running",
                data=common,
            )
        )
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=child.task_id,
                parent_task_id=parent.task_id,
                type=EventType.TASK_QUEUED.value,
                event_id=f"{child.task_id}:task.queued",
                status="queued",
                data={"reason": "bounded_loop_iteration", **common},
            )
        )
        if child.status == "completed":
            await runtime.events.publish(
                AgentEvent(
                    run_id=run.run_id,
                    task_id=parent.task_id,
                    type=EventType.LOOP_ITERATION_COMPLETED.value,
                    event_id=f"{child.task_id}:loop.iteration_completed",
                    status=child.status,
                    data={**common, "status": child.status},
                )
            )


def _result(
    *,
    status: str,
    stop_reason: str,
    loop_id: str,
    children: list[Any],
    state: Any | None = None,
    exited: bool = False,
) -> dict[str, Any]:
    structured = {
        "state": state,
        "iterations": len(children),
        "exited": exited,
    }
    return {
        "status": status,
        "stop_reason": stop_reason,
        "loop_id": loop_id,
        "iteration_count": len(children),
        "child_task_ids": [child.task_id for child in children],
        "structured_output": structured,
        "content": json.dumps(structured, ensure_ascii=False, sort_keys=True),
        "usage": _usage(children),
        "tools_used": sorted(
            {
                str(tool)
                for child in children
                for tool in (dict(child.result or {}).get("tools_used") or [])
            }
        ),
    }


async def execute_graph_bounded_loop(
    runtime: Any,
    run: Any,
    task: Any,
    dependency_results: dict[str, dict[str, Any]],
) -> None:
    configuration = dict(task.payload["bounded_loop"])
    initial_state = select_initial_loop_state(configuration, dependency_results)
    loop_id = _loop_identity(task, configuration, initial_state)
    all_tasks = await asyncio.to_thread(
        runtime.store.list_runtime_tasks, run_id=run.run_id, limit=5000
    )
    children = sorted(
        [item for item in all_tasks if item.parent_task_id == task.task_id],
        key=lambda item: (int(item.payload["bounded_loop_iteration"]), item.task_id),
    )
    for expected, child in enumerate(children, start=1):
        if (
            int(child.payload.get("bounded_loop_iteration") or 0) != expected
            or str(child.payload.get("bounded_loop_id") or "") != loop_id
        ):
            raise RuntimeError("bounded_loop durable iteration ledger is inconsistent")
    await _publish_iteration_ledger(runtime, run, task, children)
    if not children:
        await _advance(runtime, run, task, loop_id, initial_state, 1, None)
        return
    latest = children[-1]
    if latest.status != "completed":
        if latest.status not in _TERMINAL:
            raise RuntimeError("bounded_loop parent resumed before child became terminal")
        value = _result(
            status="failed",
            stop_reason="bounded_loop_iteration_failed",
            loop_id=loop_id,
            children=children,
        )
        value["failed_child_task_id"] = latest.task_id
        message = str((latest.error or {}).get("message") or "loop iteration failed")
        await _finish(runtime, run, task, children, "iteration_failed", value, message)
        return
    latest_result = dict(latest.result or {})
    next_state = select_next_loop_state(configuration, latest_result)
    if loop_should_exit(configuration, latest_result):
        value = _result(
            status="completed",
            stop_reason="bounded_loop_completed",
            loop_id=loop_id,
            children=children,
            state=next_state,
            exited=True,
        )
        await _finish(runtime, run, task, children, "completed", value, None)
        return
    if len(children) >= int(configuration["max_iterations"]):
        value = _result(
            status="failed",
            stop_reason="bounded_loop_exhausted",
            loop_id=loop_id,
            children=children,
            state=next_state,
        )
        message = f"bounded_loop exhausted after {len(children)} iterations"
        exhausted_event = AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.LOOP_EXHAUSTED.value,
            event_id=f"{task.task_id}:loop.exhausted",
            status="failed",
            data={
                "loop_id": loop_id,
                "max_iterations": int(configuration["max_iterations"]),
                "iteration_count": len(children),
            },
        )
        await _finish(
            runtime,
            run,
            task,
            children,
            "exhausted",
            value,
            message,
            extra_events=[exhausted_event],
        )
        return
    await _advance(
        runtime,
        run,
        task,
        loop_id,
        next_state,
        len(children) + 1,
        latest.task_id,
    )


async def _advance(
    runtime: Any,
    run: Any,
    task: Any,
    loop_id: str,
    state: Any,
    iteration: int,
    previous_child_id: str | None,
) -> None:
    child = _loop_child(task, state=state, iteration=iteration, loop_id=loop_id)
    outcome = await asyncio.to_thread(
        runtime.store.advance_runtime_bounded_loop,
        run_id=run.run_id,
        task_id=task.task_id,
        loop_id=loop_id,
        iteration=iteration,
        input_state_hash=child["payload"]["bounded_loop_input_state_hash"],
        previous_child_id=previous_child_id,
        child=child,
        worker_id=runtime.worker_id,
        lease_version=task.lease_version,
    )
    if not outcome["saved"]:
        raise asyncio.CancelledError("bounded_loop advance fenced by a newer lease")
    await _publish_iteration_ledger(
        runtime,
        run,
        task,
        [SimpleNamespace(**child, status="queued", result=None)],
    )


async def _finish(
    runtime: Any,
    run: Any,
    task: Any,
    children: list[Any],
    outcome: str,
    result: dict[str, Any],
    error: str | None,
    extra_events: list[AgentEvent] | None = None,
) -> None:
    terminal_event = AgentEvent(
        run_id=run.run_id,
        task_id=task.task_id,
        type=(
            EventType.TASK_COMPLETED.value
            if outcome == "completed"
            else EventType.TASK_FAILED.value
        ),
        event_id=f"{task.task_id}:bounded_loop.{outcome}",
        status=result["status"],
        data={**result, "error": error},
    )
    prepared_events = [
        await runtime.events.prepare(event)
        for event in [terminal_event, *(extra_events or [])]
    ]
    saved = await asyncio.to_thread(
        runtime.store.finish_runtime_bounded_loop,
        run_id=run.run_id,
        task_id=task.task_id,
        outcome=outcome,
        child_task_ids=[child.task_id for child in children],
        result=result,
        error={"message": error} if error else None,
        events=[event.to_dict() for event in prepared_events],
        worker_id=runtime.worker_id,
        lease_version=task.lease_version,
    )
    if not saved:
        raise asyncio.CancelledError("bounded_loop completion fenced by a newer lease")
    for event in prepared_events:
        await runtime.events.publish(event)
