"""Runtime execution for explicit Graph approval and verification nodes."""

from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from typing import Any

from joyhousebot.orchestration.control_nodes import control_source_id
from joyhousebot.runtime.action_identity import payload_hash
from joyhousebot.runtime.context import RunContext, VerificationFailedError
from joyhousebot.runtime.models import AgentEvent, EventType, TaskStatus
from joyhousebot.runtime.verification import verify_output


async def execute_graph_approval(
    runtime: Any,
    run: Any,
    task: Any,
    dependency_results: dict[str, dict[str, Any]],
) -> None:
    configuration = dict(task.payload.get("approval") or {})
    dependencies = [
        {
            "node_id": node_id,
            "result_hash": payload_hash(result),
        }
        for node_id, result in sorted(dependency_results.items())
    ]
    frozen_input = {
        "graph_revision_id": str(task.payload.get("graph_revision_id") or ""),
        "node_id": str(task.payload.get("spec_id") or task.task_id),
        "dependencies": dependencies,
    }
    input_hash = payload_hash(frozen_input)
    identity = "\0".join([run.run_id, task.task_id, str(task.lease_version), input_hash]).encode(
        "utf-8"
    )
    approval_id = f"apr_graph_{sha256(identity).hexdigest()}"
    title = str(configuration.get("title") or task.name or task.payload.get("spec_id"))
    description = str(configuration.get("description") or task.payload.get("prompt") or "")
    subject = {
        **frozen_input,
        "title": title,
        "description": description,
    }
    record = await asyncio.to_thread(
        runtime.stores.graphs.suspend_graph_task_for_explicit_approval,
        run_id=run.run_id,
        task_id=task.task_id,
        approval_id=approval_id,
        subject=subject,
        input_hash=input_hash,
        input_preview={"title": title, "dependencies": dependencies},
        risk=str(configuration.get("risk") or "medium"),
        data_classification=str(configuration.get("data_classification") or "internal"),
        required_role=str(configuration.get("required_role") or "owner"),
        requested_by=task.agent_id,
        expires_in_seconds=int(configuration.get("expires_in_seconds") or 86_400),
        worker_id=runtime.worker_id,
        lease_version=task.lease_version,
    )
    if record is None:
        raise asyncio.CancelledError("approval suspension fenced by a newer Task lease")
    data = {
        "approval_id": record.approval_id,
        "required_role": record.required_role,
        "risk": record.risk,
        "title": title,
        "waiting_on": record.approval_id,
    }
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.APPROVAL_REQUESTED.value,
            event_id=f"{record.approval_id}:requested",
            status="pending",
            data=data,
        )
    )
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.RUN_WAITING_APPROVAL.value,
            event_id=f"{record.approval_id}:waiting",
            status="waiting_approval",
            data=data,
        )
    )
    await runtime._log(
        run.run_id,
        "graph.approval.requested",
        "Explicit Graph approval requested",
        task_id=task.task_id,
        data=data,
    )


async def execute_graph_verify(
    runtime: Any,
    run: Any,
    task: Any,
    dependency_results: dict[str, dict[str, Any]],
) -> None:
    source_id = control_source_id(dict(task.payload.get("verify") or {}))
    source = dependency_results[source_id]
    content = source.get("content")
    if content is None and source.get("structured_output") is not None:
        content = json.dumps(source["structured_output"], ensure_ascii=False, sort_keys=True)
    if content is not None and not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)

    async def _event(kind: str, data: dict[str, Any]) -> None:
        event_type = {
            "verification_started": EventType.VERIFICATION_STARTED.value,
            "verification_passed": EventType.VERIFICATION_PASSED.value,
            "verification_failed": EventType.VERIFICATION_FAILED.value,
        }[kind]
        await runtime.events.publish(
            AgentEvent(
                run_id=run.run_id,
                task_id=task.task_id,
                type=event_type,
                event_id=f"{data['verification_id']}:{event_type}",
                status=("failed" if kind == "verification_failed" else "completed"),
                data={**data, "source_task_id": f"{run.run_id}:{source_id}"},
            )
        )

    schema = dict(task.payload["output_schema"]) if task.payload.get("output_schema") else None
    context = RunContext(
        run_id=run.run_id,
        task_id=task.task_id,
        root_run_id=run.root_run_id or run.run_id,
        user_id=run.user_id,
        agent_id=task.agent_id,
        session_key=f"{run.user_id}:{task.agent_id}:{run.session_id}",
        session_id=run.session_id,
        channel="runtime",
        chat_id=str(task.payload.get("spec_id") or task.task_id),
        trace_store=runtime.stores.execution,
        output_schema=schema,
        verification_policy=dict(task.payload.get("verification_policy") or {}),
        worker_id=runtime.worker_id,
        task_lease_version=task.lease_version,
        metadata={"verification_source_task_id": f"{run.run_id}:{source_id}"},
    )
    decision = await verify_output(
        context,
        content,
        turn_id=None,
        attempt=task.attempt,
        event_callback=_event,
    )
    if not decision.passed:
        raise VerificationFailedError(decision.failures, decision.attempt)
    structured = (
        decision.structured_output
        if decision.structured_output is not None
        else source.get("structured_output")
    )
    result = {
        "status": "completed",
        "node_type": "verify",
        "source_task_id": f"{run.run_id}:{source_id}",
        "content": content,
        "structured_output": structured,
        "verification_attempt": decision.attempt,
        "verification_input_hash": decision.input_hash,
    }
    saved = await asyncio.to_thread(
        runtime.stores.tasks.update_runtime_task,
        task.task_id,
        status=TaskStatus.COMPLETED.value,
        result=result,
        worker_id=runtime.worker_id,
        lease_version=task.lease_version,
    )
    if not saved:
        raise asyncio.CancelledError("verify completion fenced by a newer Task lease")
    await runtime.events.publish(
        AgentEvent(
            run_id=run.run_id,
            task_id=task.task_id,
            type=EventType.TASK_COMPLETED.value,
            status="completed",
            data={
                "node_type": "verify",
                "source_task_id": result["source_task_id"],
                "verification_attempt": decision.attempt,
            },
        )
    )
    await runtime._log(
        run.run_id,
        "graph.verify.completed",
        "Explicit Graph verification passed",
        task_id=task.task_id,
        data={
            "source_task_id": result["source_task_id"],
            "verification_attempt": decision.attempt,
        },
    )
