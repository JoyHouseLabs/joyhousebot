"""Run and DAG HTTP endpoints."""

from __future__ import annotations

import re

from fastapi import APIRouter, Header, Query, Request, Response

from joyhousebot.api.dependencies import ContainerDep, ContextDep
from joyhousebot.api.schemas import CreateGraphRequest, CreateRunRequest, ResolveRunInputRequest
from joyhousebot.application.presenters import record_dict
from joyhousebot.application.runs import CreateRunCommand, GraphTaskCommand
from joyhousebot.runtime.narrative import public_event_dict

router = APIRouter(prefix="/runs", tags=["runs"])


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
            timeout_seconds=body.timeout_seconds,
            max_turns=body.max_turns,
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


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, context: ContextDep, container: ContainerDep):
    return record_dict(await container.runs.cancel(context, run_id))


@router.post("/{run_id}/resume", status_code=202)
async def resume_run(run_id: str, context: ContextDep, container: ContainerDep):
    return record_dict(await container.runs.resume(context, run_id))


@router.get("/{run_id}/tasks")
async def list_tasks(run_id: str, context: ContextDep, container: ContainerDep):
    return {"items": [record_dict(row) for row in await container.runs.tasks(context, run_id)]}


@router.get("/{run_id}/artifacts")
async def list_artifacts(run_id: str, context: ContextDep, container: ContainerDep):
    return {"items": await container.runs.artifacts(context, run_id)}


@router.get("/{run_id}/invocations")
async def list_invocations(run_id: str, context: ContextDep, container: ContainerDep):
    rows = await container.runs.invocations(context, run_id)
    return {"items": [record_dict(row) for row in rows]}


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
