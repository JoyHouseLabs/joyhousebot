"""Platform administration, global monitoring, and safe configuration views."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from joyhousebot.api.dependencies import (
    AdminsReaderDep,
    AdminWriterDep,
    AuditReaderDep,
    ContainerDep,
    PlatformAdminDep,
    RawTraceReaderDep,
    ReasoningReaderDep,
    ReplayReaderDep,
    ReplayWriterDep,
    RunsCancellerDep,
    RunsReaderDep,
    TokensReaderDep,
    TokensWriterDep,
)
from joyhousebot.api.schemas import (
    CreateAccessTokenRequest,
    CreateReplayRequest,
    SavePlatformAdminRequest,
)
from joyhousebot.application.presenters import record_dict, runtime_run_list_item
from joyhousebot.runtime.narrative import public_event_dict

router = APIRouter(prefix="/admin", tags=["platform-admin"])


@router.get("/overview")
async def overview(principal: PlatformAdminDep, container: ContainerDep):
    return await asyncio.to_thread(container.store.get_platform_overview)


@router.get("/runs")
async def list_runs(
    principal: RunsReaderDep,
    container: ContainerDep,
    user_id: str | None = None,
    session_id: str | None = None,
    agent_id: str | None = None,
    status: str | None = None,
    search: str | None = Query(default=None, max_length=500),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=100),
):
    filters = {
        "user_id": user_id,
        "session_id": session_id,
        "agent_id": agent_id,
        "status": status,
        "search": search,
    }
    rows, total = await asyncio.gather(
        asyncio.to_thread(
        container.store.list_runtime_runs,
        **filters,
        limit=limit,
        offset=(page - 1) * limit,
        ),
        asyncio.to_thread(container.store.count_runtime_runs, **filters),
    )
    return {
        "items": [runtime_run_list_item(row) for row in rows],
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": max(1, (total + limit - 1) // limit),
        },
    }


async def _admin_run(container, run_id: str):
    record = await asyncio.to_thread(container.store.get_runtime_run, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")
    return record


@router.get("/runs/{run_id}")
async def get_run(run_id: str, principal: RunsReaderDep, container: ContainerDep):
    return record_dict(await _admin_run(container, run_id))


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, principal: RunsCancellerDep, container: ContainerDep):
    await _admin_run(container, run_id)
    if not await container.runtime.cancel(run_id, f"cancelled by {principal.subject}"):
        raise HTTPException(status_code=409, detail="run is not cancellable")
    return record_dict(await _admin_run(container, run_id))


@router.get("/runs/{run_id}/diagnostics")
async def run_diagnostics(
    run_id: str,
    principal: RunsReaderDep,
    container: ContainerDep,
    after_sequence: int = Query(default=0, ge=0),
):
    run = await _admin_run(container, run_id)
    tracker_id = str((run.options or {}).get("tracker_id") or "")
    (
        tasks,
        logs,
        events,
        invocations,
        artifacts,
        spans,
        model_invocations,
        reasoning,
        blobs,
        replays,
        feedback,
    ) = await asyncio.gather(
        asyncio.to_thread(container.store.list_runtime_tasks, run_id=run_id, limit=5000),
        asyncio.to_thread(container.store.list_runtime_logs, run_id, after_sequence=after_sequence),
        asyncio.to_thread(
            container.store.list_runtime_events,
            run_id,
            after_sequence=after_sequence,
            limit=5000,
        ),
        asyncio.to_thread(container.store.list_capability_invocations, run_id),
        asyncio.to_thread(container.store.list_runtime_artifacts, run_id),
        asyncio.to_thread(container.store.list_execution_spans, run_id),
        asyncio.to_thread(container.store.list_model_invocations, run_id),
        asyncio.to_thread(container.store.list_reasoning_segments, run_id),
        asyncio.to_thread(container.store.list_trace_blobs, run_id),
        asyncio.to_thread(container.store.list_replay_runs, run_id),
        asyncio.to_thread(container.store.list_run_feedback, run_id, limit=5000),
    )
    traces = (
        await asyncio.to_thread(
            container.store.list_request_trace_events, tracker_id, limit=10000
        )
        if tracker_id
        else []
    )
    children = await asyncio.to_thread(
        container.store.list_runtime_runs, parent_run_id=run_id, limit=1000
    )
    if reasoning and principal.can("reasoning.read"):
        await asyncio.to_thread(
            container.store.append_runtime_log,
            run_id=run_id,
            stage="reasoning.accessed",
            message="Reasoning included in platform diagnostics",
            data={"actor": principal.subject, "segment_count": len(reasoning)},
        )
    return {
        "run": record_dict(run),
        "tasks": [record_dict(item) for item in tasks],
        "events": [
            item.to_dict() if principal.can("reasoning.read") else public_event_dict(item)
            for item in events
        ],
        "logs": [record_dict(item) for item in logs],
        "invocations": [record_dict(item) for item in invocations],
        "artifacts": artifacts,
        "traces": [record_dict(item) for item in traces],
        "children": [record_dict(item) for item in children],
        "spans": [item.to_dict() for item in spans],
        "model_invocations": [item.to_dict() for item in model_invocations],
        "reasoning": (
            [item.to_dict() for item in reasoning]
            if principal.can("reasoning.read")
            else []
        ),
        "trace_blobs": [item.to_dict(include_content=False) for item in blobs],
        "replays": [item.to_dict() for item in replays],
        "feedback": [item.to_dict() for item in feedback],
    }


@router.get("/runs/{run_id}/reasoning")
async def run_reasoning(
    run_id: str,
    principal: ReasoningReaderDep,
    container: ContainerDep,
    invocation_id: str | None = None,
):
    await _admin_run(container, run_id)
    rows = await asyncio.to_thread(
        container.store.list_reasoning_segments,
        run_id,
        invocation_id=invocation_id,
    )
    await asyncio.to_thread(
        container.store.append_runtime_log,
        run_id=run_id,
        stage="reasoning.accessed",
        message="Raw reasoning viewed by platform administrator",
        data={"actor": principal.subject, "invocation_id": invocation_id},
    )
    return {"items": [item.to_dict() for item in rows]}


@router.get("/runs/{run_id}/blobs/{blob_id}")
async def trace_blob(
    run_id: str,
    blob_id: str,
    principal: RawTraceReaderDep,
    container: ContainerDep,
):
    await _admin_run(container, run_id)
    blob = await asyncio.to_thread(container.store.get_trace_blob, blob_id)
    if blob is None or blob.run_id != run_id:
        raise HTTPException(status_code=404, detail="trace blob not found")
    await asyncio.to_thread(
        container.store.append_runtime_log,
        run_id=run_id,
        stage="trace_blob.accessed",
        message="Full-fidelity trace payload viewed",
        data={"actor": principal.subject, "blob_id": blob_id, "kind": blob.kind},
    )
    return blob.to_dict(include_content=True)


def _replay_comparison(source, target=None) -> dict:
    source_content = str(((source.result or {}).get("content") or ""))
    target_content = str(((target.result or {}).get("content") or "")) if target else source_content
    return {
        "source_status": source.status,
        "target_status": target.status if target else source.status,
        "content_equal": source_content == target_content,
        "source_length": len(source_content),
        "target_length": len(target_content),
    }


@router.post("/runs/{run_id}/replays", status_code=202)
async def create_replay(
    run_id: str,
    body: CreateReplayRequest,
    principal: ReplayWriterDep,
    container: ContainerDep,
):
    from joyhousebot.runtime.models import AgentOptions

    source = await _admin_run(container, run_id)
    overrides = {
        key: value
        for key, value in {
            "prompt": body.prompt,
            "model": body.model,
            "agent_id": body.agent_id,
            "system_prompt": body.system_prompt,
        }.items()
        if value is not None
    }
    replay = await asyncio.to_thread(
        container.store.create_replay_run,
        source_run_id=run_id,
        source_turn_id=body.source_turn_id,
        mode=body.mode,
        overrides=overrides,
        created_by=principal.subject,
        status="completed" if body.mode in {"offline", "frozen"} else "queued",
        comparison=(
            _replay_comparison(source) if body.mode in {"offline", "frozen"} else None
        ),
        finished_at=(
            source.updated_at if body.mode in {"offline", "frozen"} else None
        ),
    )
    await asyncio.to_thread(
        container.store.append_runtime_log,
        run_id=run_id,
        stage="replay.created",
        message="Replay experiment created",
        data={"actor": principal.subject, "replay_id": replay.replay_id, "mode": body.mode},
    )
    if body.mode in {"offline", "frozen"}:
        return replay.to_dict()

    values = dict(source.options or {})
    metadata = {
        **dict(values.get("metadata") or {}),
        "replay": {
            "replay_id": replay.replay_id,
            "source_run_id": source.run_id,
            "source_turn_id": body.source_turn_id,
            "mode": body.mode,
        },
    }
    options = AgentOptions.from_dict(
        {
            **values,
            "prompt": body.prompt if body.prompt is not None else source.prompt,
            "user_id": source.user_id,
            "session_id": source.session_id,
            "agent_id": body.agent_id or source.agent_id,
            "model": body.model if body.model is not None else values.get("model"),
            "system_prompt": (
                body.system_prompt
                if body.system_prompt is not None
                else values.get("system_prompt")
            ),
            "metadata": metadata,
            "idempotency_key": None,
            "root_run_id": source.root_run_id or source.run_id,
            "parent_run_id": source.run_id,
            "parent_task_id": None,
            "request_id": None,
            "tracker_id": None,
        }
    )
    new_run = await container.runtime.submit_run(options)
    await asyncio.to_thread(
        container.store.update_replay_run,
        replay.replay_id,
        new_run_id=new_run.run_id,
        status="running",
        finished_at=None,
    )
    replay = await asyncio.to_thread(container.store.get_replay_run, replay.replay_id)
    return replay.to_dict()


@router.get("/runs/{run_id}/replays")
async def list_replays(
    run_id: str,
    principal: ReplayReaderDep,
    container: ContainerDep,
):
    source = await _admin_run(container, run_id)
    rows = await asyncio.to_thread(container.store.list_replay_runs, run_id)
    output = []
    for item in rows:
        target = (
            await asyncio.to_thread(container.store.get_runtime_run, item.new_run_id)
            if item.new_run_id
            else None
        )
        if target and target.status in {"completed", "failed", "cancelled", "timed_out"}:
            comparison = _replay_comparison(source, target)
            if item.status != "completed" or item.comparison != comparison:
                await asyncio.to_thread(
                    container.store.update_replay_run,
                    item.replay_id,
                    new_run_id=target.run_id,
                    status="completed",
                    comparison=comparison,
                )
                item = await asyncio.to_thread(
                    container.store.get_replay_run, item.replay_id
                )
        output.append(item.to_dict())
    return {"items": output}


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    principal: RunsReaderDep,
    container: ContainerDep,
    after_sequence: int = Query(default=0, ge=0),
):
    await _admin_run(container, run_id)

    async def generate():
        async for event in container.runtime.events.subscribe(
            run_id, after_sequence=after_sequence
        ):
            if await request.is_disconnected():
                return
            value = (
                event.to_dict()
                if principal.can("reasoning.read")
                else public_event_dict(event)
            )
            payload = json.dumps(value, ensure_ascii=False)
            yield f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n"
            if event.type in {"run.completed", "run.failed", "run.cancelled", "run.timed_out"}:
                return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/users")
async def list_admins(principal: AdminsReaderDep, container: ContainerDep):
    rows = await asyncio.to_thread(container.store.list_platform_admins)
    return {"items": [item.to_dict() for item in rows]}


@router.get("/access-tokens")
async def list_access_tokens(principal: TokensReaderDep, container: ContainerDep):
    rows = await asyncio.to_thread(container.store.list_api_access_tokens, limit=5000)
    return {"items": rows}


@router.post("/access-tokens", status_code=201)
async def create_access_token(
    body: CreateAccessTokenRequest,
    principal: TokensWriterDep,
    container: ContainerDep,
):
    record, token = await asyncio.to_thread(
        container.store.create_api_access_token,
        user_id=body.user_id,
        label=body.label,
        expires_at=body.expires_at,
        actor_id=principal.subject,
    )
    return {**record, "token": token}


@router.delete("/access-tokens/{token_id}")
async def revoke_access_token(
    token_id: str,
    principal: TokensWriterDep,
    container: ContainerDep,
):
    revoked = await asyncio.to_thread(
        container.store.revoke_api_access_token, token_id, actor_id=principal.subject
    )
    if not revoked:
        raise HTTPException(status_code=404, detail="access token not found or already revoked")
    return {"revoked": True}


@router.put("/users/{user_id}")
async def save_admin(
    user_id: str,
    body: SavePlatformAdminRequest,
    principal: AdminWriterDep,
    container: ContainerDep,
):
    if principal.user_id == user_id and (
        not body.enabled
        or ("*" not in body.permissions and "admins.write" not in body.permissions)
    ):
        raise HTTPException(
            status_code=409,
            detail="cannot remove administrator authority from the active administrator",
        )
    record = await asyncio.to_thread(
        container.store.upsert_platform_admin,
        user_id=user_id,
        role=body.role,
        permissions=body.permissions,
        enabled=body.enabled,
        is_test_user=body.is_test_user,
        actor_id=principal.subject,
    )
    return record.to_dict()


@router.delete("/users/{user_id}")
async def delete_admin(
    user_id: str,
    principal: AdminWriterDep,
    container: ContainerDep,
):
    if principal.user_id == user_id:
        raise HTTPException(status_code=409, detail="cannot remove the active administrator")
    deleted = await asyncio.to_thread(
        container.store.delete_platform_admin, user_id, actor_id=principal.subject
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="administrator not found")
    return {"deleted": True}


@router.get("/access-events")
async def access_events(
    principal: AuditReaderDep,
    container: ContainerDep,
    limit: int = Query(default=200, ge=1, le=2000),
):
    rows = await asyncio.to_thread(
        container.store.list_platform_admin_events, limit=limit
    )
    return {"items": rows}
