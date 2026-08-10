"""Run and DAG HTTP endpoints."""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Header, Query, Request, Response

from joyhousebot.api.dependencies import ContainerDep, ContextDep
from joyhousebot.api.schemas import (
    CreateGraphPatchRequest,
    CreateGraphRequest,
    CreateRunFeedbackRequest,
    CreateRunRequest,
    ResolveApprovalRequest,
    ResolveGraphPatchProposalRequest,
    ResolveOperationRequest,
    ResolveRunInputRequest,
)
from joyhousebot.application.context_manifests import context_manifest_public_dict
from joyhousebot.application.feedback import CreateFeedbackCommand
from joyhousebot.application.graph_events import graph_event_wait_public_dict
from joyhousebot.application.graph_patch_commands import (
    ApplyGraphPatchCommand,
    GraphPatchOperationCommand,
    ResolveGraphPatchProposalCommand,
)
from joyhousebot.application.loop_decisions import loop_decision_public_dict
from joyhousebot.application.presenters import record_dict
from joyhousebot.application.runs import CreateRunCommand, GraphTaskCommand
from joyhousebot.application.verifications import verification_public_dict
from joyhousebot.runtime.narrative import public_event_dict

router = APIRouter(prefix="/runs", tags=["runs"])


def _graph_patch_proposal_public(record):  # noqa: ANN001, ANN201
    value = record_dict(record)
    value.pop("candidate_revision", None)
    value.pop("task_rows", None)
    value.pop("lease_owner", None)
    return value


def _prefer_wait(value: str | None) -> float:
    match = re.search(r"(?:^|,)\s*wait=(\d+(?:\.\d+)?)", value or "", re.I)
    return min(30.0, float(match.group(1))) if match else 0.0


@router.post("", status_code=202)
async def create_run(
    body: CreateRunRequest,
    context: ContextDep,
    container: ContainerDep,
    response: Response,
    prefer: str | None = Header(default=None),
):
    record = await container.runs.create(
        context,
        CreateRunCommand(
            agent_id=body.agent_id,
            session_id=body.session_id,
            scenario_id=body.scenario_id,
            scenario_inputs=body.scenario_inputs,
            execution_mode=body.execution_mode,
            input=body.input.content,
            model=body.model,
            system_prompt=body.system_prompt,
            output_schema=body.output_schema,
            verification_policy=body.verification_policy,
            timeout_seconds=body.timeout_seconds,
            max_turns=body.max_turns,
            max_repairs=body.max_repairs,
            max_replans=body.max_replans,
            metadata=body.metadata,
        ),
    )
    response.headers["Location"] = f"/v1/runs/{record.run_id}"
    wait_seconds = _prefer_wait(prefer)
    if wait_seconds > 0:
        response.headers["Preference-Applied"] = f"wait={wait_seconds:g}"
    if (
        wait_seconds > 0
        and body.execution_mode != "background"
        and record.status
        not in {
            "waiting_input",
            "waiting_approval",
            "waiting_external",
            "scheduled",
            "paused",
        }
    ):
        record = await container.runtime.wait(record.run_id, timeout=wait_seconds)
    if record is not None and record.status in {"completed", "failed", "cancelled", "timed_out"}:
        response.status_code = 200
    return record_dict(record)


@router.post("/graphs", status_code=202)
async def create_graph(body: CreateGraphRequest, context: ContextDep, container: ContainerDep):
    record = await container.runs.create_graph(
        context,
        goal=body.goal,
        agent_id=body.agent_id,
        session_id=body.session_id,
        max_concurrent=body.max_concurrent,
        fail_fast=body.fail_fast,
        failure_policy=body.failure_policy,
        aggregate=body.aggregate,
        aggregation_policy=body.aggregation_policy,
        max_input_tokens=body.max_input_tokens,
        max_output_tokens=body.max_output_tokens,
        max_cost_usd=body.max_cost_usd,
        tasks=[GraphTaskCommand(**item.model_dump()) for item in body.tasks],
    )
    return record_dict(record)


@router.get("")
async def list_runs(
    context: ContextDep,
    container: ContainerDep,
    session_id: str | None = None,
    agent_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
):
    rows = await container.runs.list(
        context,
        session_id=session_id,
        agent_id=agent_id,
        status=status,
        limit=limit,
    )
    return {"items": [record_dict(row) for row in rows]}


@router.get("/{run_id}")
async def get_run(run_id: str, context: ContextDep, container: ContainerDep):
    return record_dict(await container.runs.get(context, run_id))


@router.get("/{run_id}/feedback")
async def list_feedback(run_id: str, context: ContextDep, container: ContainerDep):
    """List feedback visible to the owner of this Run."""
    await container.runs.get(context, run_id)
    rows = await asyncio.to_thread(
        container.store.list_run_feedback,
        run_id,
        user_id=context.user_id,
        limit=200,
    )
    return {"items": [record_dict(row) for row in rows]}


@router.post("/{run_id}/feedback", status_code=201)
async def create_feedback(
    run_id: str,
    body: CreateRunFeedbackRequest,
    context: ContextDep,
    container: ContainerDep,
):
    """Persist human feedback with the Run's execution snapshot for audit/replay."""
    row = await container.feedback.create(
        context,
        run_id,
        CreateFeedbackCommand(**body.model_dump()),
    )
    return record_dict(row)


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, context: ContextDep, container: ContainerDep):
    return record_dict(await container.runs.cancel(context, run_id))


@router.post("/{run_id}/resume", status_code=202)
async def resume_run(run_id: str, context: ContextDep, container: ContainerDep):
    return record_dict(await container.runs.resume(context, run_id))


@router.get("/{run_id}/tasks")
async def list_tasks(run_id: str, context: ContextDep, container: ContainerDep):
    return {"items": [record_dict(row) for row in await container.runs.tasks(context, run_id)]}


@router.get("/{run_id}/graph-revisions")
async def list_graph_revisions(run_id: str, context: ContextDep, container: ContainerDep):
    rows = await container.runs.graph_revisions(context, run_id)
    return {"items": [record_dict(row) for row in rows]}


@router.get("/{run_id}/graph-patches")
async def list_graph_patches(run_id: str, context: ContextDep, container: ContainerDep):
    rows = await container.graph_patches.list(context, run_id)
    return {"items": [record_dict(row) for row in rows]}


@router.get("/{run_id}/graph-patch-proposals")
async def list_graph_patch_proposals(
    run_id: str, context: ContextDep, container: ContainerDep
):
    rows = await container.graph_patches.list_proposals(context, run_id)
    return {"items": [_graph_patch_proposal_public(row) for row in rows]}


@router.post("/{run_id}/graph-patch-proposals", status_code=201)
async def propose_graph_patch(
    run_id: str,
    body: CreateGraphPatchRequest,
    context: ContextDep,
    container: ContainerDep,
):
    proposal, run = await container.graph_patches.propose(
        context,
        run_id,
        ApplyGraphPatchCommand(
            base_revision_id=body.base_revision_id,
            reason=body.reason,
            operations=tuple(
                GraphPatchOperationCommand(
                    op=item.op,
                    node=GraphTaskCommand(**item.node.model_dump()),
                )
                for item in body.operations
            ),
        ),
    )
    return {"proposal": _graph_patch_proposal_public(proposal), "run": record_dict(run)}


@router.post("/{run_id}/graph-patch-proposals/{proposal_id}/resolve")
async def resolve_graph_patch_proposal(
    run_id: str,
    proposal_id: str,
    body: ResolveGraphPatchProposalRequest,
    context: ContextDep,
    container: ContainerDep,
):
    proposal, run = await container.graph_patches.resolve_proposal(
        context,
        run_id,
        proposal_id,
        ResolveGraphPatchProposalCommand(
            resolution=body.resolution,
            note=body.note,
        ),
    )
    return {
        "proposal": _graph_patch_proposal_public(proposal),
        "run": record_dict(run) if run is not None else None,
    }


@router.post("/{run_id}/graph-patches", status_code=201)
async def apply_graph_patch(
    run_id: str,
    body: CreateGraphPatchRequest,
    context: ContextDep,
    container: ContainerDep,
):
    patch, run = await container.graph_patches.apply(
        context,
        run_id,
        ApplyGraphPatchCommand(
            base_revision_id=body.base_revision_id,
            reason=body.reason,
            approve_high_risk=body.approve_high_risk,
            operations=tuple(
                GraphPatchOperationCommand(
                    op=item.op,
                    node=GraphTaskCommand(**item.node.model_dump()),
                )
                for item in body.operations
            ),
        ),
    )
    return {"patch": record_dict(patch), "run": record_dict(run)}


@router.get("/{run_id}/event-waits")
async def list_graph_event_waits(run_id: str, context: ContextDep, container: ContainerDep):
    rows = await container.graph_events.list(context, run_id)
    return {"items": [graph_event_wait_public_dict(row) for row in rows]}


@router.post("/{run_id}/event-waits/{wait_id}/token", status_code=201)
async def issue_graph_event_token(
    run_id: str, wait_id: str, context: ContextDep, container: ContainerDep
):
    record, token = await container.graph_events.issue_token(context, run_id, wait_id)
    return {"wait": graph_event_wait_public_dict(record), "token": token}


@router.get("/{run_id}/artifacts")
async def list_artifacts(run_id: str, context: ContextDep, container: ContainerDep):
    return {"items": await container.runs.artifacts(context, run_id)}


@router.get("/{run_id}/invocations")
async def list_invocations(run_id: str, context: ContextDep, container: ContainerDep):
    rows = await container.runs.invocations(context, run_id)
    return {"items": [record_dict(row) for row in rows]}


@router.get("/{run_id}/verifications")
async def list_verifications(run_id: str, context: ContextDep, container: ContainerDep):
    await container.runs.get(context, run_id)
    rows = await asyncio.to_thread(
        container.store.list_verification_records,
        run_id,
        expected_user_id=context.user_id,
    )
    return {"items": [verification_public_dict(row) for row in rows]}


@router.get("/{run_id}/decisions")
async def list_loop_decisions(run_id: str, context: ContextDep, container: ContainerDep):
    await container.runs.get(context, run_id)
    rows = await asyncio.to_thread(
        container.store.list_loop_decisions,
        run_id,
        expected_user_id=context.user_id,
    )
    return {"items": [loop_decision_public_dict(row) for row in rows]}


@router.get("/{run_id}/context-manifest")
async def list_context_manifests(run_id: str, context: ContextDep, container: ContainerDep):
    """Return source hashes and budget evidence, never raw model context."""
    await container.runs.get(context, run_id)
    rows = await asyncio.to_thread(
        container.store.list_context_manifests,
        run_id,
        expected_user_id=context.user_id,
    )
    return {"items": [context_manifest_public_dict(row) for row in rows]}


@router.get("/{run_id}/inputs/pending")
async def pending_inputs(run_id: str, context: ContextDep, container: ContainerDep):
    rows = await container.runs.pending_inputs(context, run_id)
    return {"items": [record_dict(row) for row in rows]}


@router.post("/{run_id}/inputs")
async def resolve_input(
    run_id: str,
    body: ResolveRunInputRequest,
    context: ContextDep,
    container: ContainerDep,
):
    run, pending = await container.runs.resolve_input(
        context,
        run_id,
        input_request_id=body.input_request_id,
        answers=body.answers,
    )
    return {
        "run": record_dict(run),
        "pending_inputs": [record_dict(row) for row in pending],
    }


@router.get("/{run_id}/approvals")
async def list_approvals(run_id: str, context: ContextDep, container: ContainerDep):
    rows = await container.approvals.list(context, run_id)
    return {"items": [record_dict(row) for row in rows]}


@router.post("/{run_id}/approvals/{approval_id}/resolve")
async def resolve_approval(
    run_id: str,
    approval_id: str,
    body: ResolveApprovalRequest,
    context: ContextDep,
    container: ContainerDep,
):
    approval, run = await container.approvals.resolve(
        context,
        run_id,
        approval_id,
        resolution=body.resolution,
        note=body.note,
    )
    return {"approval": record_dict(approval), "run": record_dict(run)}


@router.get("/{run_id}/operations")
async def list_operations(run_id: str, context: ContextDep, container: ContainerDep):
    rows = await container.reconciliations.list(context, run_id)
    return {"items": [record_dict(row) for row in rows]}


@router.post("/{run_id}/operations/{reconciliation_id}/resolve")
async def resolve_operation(
    run_id: str,
    reconciliation_id: str,
    body: ResolveOperationRequest,
    context: ContextDep,
    container: ContainerDep,
):
    reconciliation, run = await container.reconciliations.resolve(
        context,
        run_id,
        reconciliation_id,
        resolution=body.resolution,
        summary=body.summary,
        data=body.data,
        error_code=body.error_code,
        note=body.note,
    )
    return {"reconciliation": record_dict(reconciliation), "run": record_dict(run)}


@router.get("/{run_id}/logs")
async def list_logs(
    run_id: str,
    context: ContextDep,
    container: ContainerDep,
    after_sequence: int = Query(default=0, ge=0),
):
    rows = await container.runs.logs(context, run_id, after_sequence=after_sequence)
    return {"items": [record_dict(row) for row in rows]}


@router.get("/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    context: ContextDep,
    container: ContainerDep,
    after_sequence: int = Query(default=0, ge=0),
):
    import json

    from fastapi.responses import StreamingResponse

    await container.runs.get(context, run_id)

    async def generate():
        async for event in container.runtime.events.subscribe(
            run_id, after_sequence=after_sequence
        ):
            if await request.is_disconnected():
                return
            payload = json.dumps(public_event_dict(event), ensure_ascii=False)
            yield f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n"
            if event.type in {"run.completed", "run.failed", "run.cancelled", "run.timed_out"}:
                return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
