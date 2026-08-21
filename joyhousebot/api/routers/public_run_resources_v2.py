"""Public Run actions, outputs, decisions and resumable event stream."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from joyhousebot.api.dependencies import ContainerDep, PublicContextDep
from joyhousebot.api.public_v2_errors import PUBLIC_ERROR_RESPONSES
from joyhousebot.api.public_v2_pagination import paginate_public_items
from joyhousebot.api.public_v2_presenters import (
    public_approval,
    public_artifact,
    public_event,
    public_input_request,
    public_operation_progress,
    public_run,
)
from joyhousebot.api.public_v2_schemas import (
    ApprovalDecisionResult,
    ApprovalList,
    ArtifactDescriptor,
    ArtifactList,
    DecideApprovalRequest,
    InputRequestList,
    PublicOperationProgressList,
    PublicRun,
    ResolveInputRequest,
    ResolveInputResult,
)

router = APIRouter(tags=["public-execution"], responses=PUBLIC_ERROR_RESPONSES)


@router.get("/runs/{run_id}/operations", response_model=PublicOperationProgressList)
async def list_operation_progress(
    run_id: str,
    context: PublicContextDep,
    container: ContainerDep,
):
    rows = await container.reconciliations.list(context, run_id)
    items = []
    for row in rows:
        events = await container.reconciliations.events(
            context,
            run_id,
            row.reconciliation_id,
            after_sequence=-1,
            limit=500,
        )
        items.append(public_operation_progress(row, events[-1] if events else None))
    return {"items": items}


@router.post("/runs/{run_id}/cancel", response_model=PublicRun)
async def cancel_run(
    run_id: str,
    context: PublicContextDep,
    container: ContainerDep,
):
    return public_run(await container.runs.cancel(context, run_id))


@router.get("/runs/{run_id}/artifacts", response_model=ArtifactList)
async def list_artifacts(
    run_id: str,
    context: PublicContextDep,
    container: ContainerDep,
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=2048),
):
    rows = await container.runs.artifacts(context, run_id)
    page, next_cursor = paginate_public_items(
        rows,
        key=lambda item: (str(item.get("created_at") or ""), str(item["artifact_id"])),
        limit=limit,
        cursor=cursor,
    )
    return {
        "items": [public_artifact(row) for row in page],
        "next_cursor": next_cursor,
    }


@router.get("/artifacts/{artifact_id}", response_model=ArtifactDescriptor)
async def get_artifact(
    artifact_id: str,
    context: PublicContextDep,
    container: ContainerDep,
):
    return public_artifact(await container.runs.artifact(context, artifact_id))


@router.get("/runs/{run_id}/inputs", response_model=InputRequestList)
async def list_inputs(
    run_id: str,
    context: PublicContextDep,
    container: ContainerDep,
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=2048),
):
    rows = await container.runs.pending_inputs(context, run_id)
    page, next_cursor = paginate_public_items(
        rows,
        key=lambda item: (str(item.created_at or ""), str(item.input_request_id)),
        limit=limit,
        cursor=cursor,
    )
    return {
        "items": [public_input_request(row) for row in page],
        "next_cursor": next_cursor,
    }


@router.post("/runs/{run_id}/inputs", response_model=ResolveInputResult)
async def resolve_input(
    run_id: str,
    body: ResolveInputRequest,
    context: PublicContextDep,
    container: ContainerDep,
):
    run, pending = await container.runs.resolve_input(
        context,
        run_id,
        input_request_id=body.input_request_id,
        answers=body.answers,
    )
    return {
        "run": public_run(run),
        "pending_inputs": [public_input_request(row) for row in pending],
    }


@router.get("/runs/{run_id}/approvals", response_model=ApprovalList)
async def list_approvals(
    run_id: str,
    context: PublicContextDep,
    container: ContainerDep,
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=2048),
):
    rows = await container.approvals.list(context, run_id)
    page, next_cursor = paginate_public_items(
        rows,
        key=lambda item: (str(item.requested_at or ""), str(item.approval_id)),
        limit=limit,
        cursor=cursor,
    )
    return {
        "items": [public_approval(row) for row in page],
        "next_cursor": next_cursor,
    }


@router.post(
    "/approvals/{approval_id}/decisions",
    response_model=ApprovalDecisionResult,
)
async def decide_approval(
    approval_id: str,
    body: DecideApprovalRequest,
    context: PublicContextDep,
    container: ContainerDep,
):
    approval, run = await container.approvals.resolve_by_id(
        context,
        approval_id,
        resolution=body.decision,
        note=body.note,
    )
    return {"approval": public_approval(approval), "run": public_run(run)}


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    context: PublicContextDep,
    container: ContainerDep,
    after_sequence: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    await container.runs.get(context, run_id)
    cursor = _event_cursor(after_sequence, last_event_id)
    return StreamingResponse(
        _event_stream(request, container, run_id, cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _event_cursor(after_sequence: int, last_event_id: str | None) -> int:
    if last_event_id is None or not last_event_id.strip():
        return after_sequence
    try:
        header_cursor = int(last_event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer") from exc
    if header_cursor < 0:
        raise HTTPException(status_code=400, detail="Last-Event-ID must not be negative")
    return max(after_sequence, header_cursor)


async def _event_stream(
    request: Request,
    container: object,
    run_id: str,
    cursor: int,
) -> AsyncIterator[str]:
    async for event in container.runtime.events.subscribe(  # type: ignore[attr-defined]
        run_id, after_sequence=cursor
    ):
        if await request.is_disconnected():
            return
        payload = public_event(event)
        if payload is None:
            continue
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        yield (
            f"id: {payload['sequence']}\n"
            f"event: {payload['event']}\n"
            f"data: {serialized}\n\n"
        )


__all__ = ["router"]
