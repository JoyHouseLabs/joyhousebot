"""User schedule HTTP endpoints."""

from typing import Literal

from fastapi import APIRouter, Query, Response

from joyhousebot.api.dependencies import ContainerDep, ContextDep
from joyhousebot.api.schemas import (
    CreateScheduleRequest,
    ResumeScheduleRequest,
    UpdateMonitorScratchRequest,
    UpdateScheduleRequest,
)

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("")
async def list_schedules(
    context: ContextDep,
    container: ContainerDep,
    include_disabled: bool = True,
    kind: Literal["agent_turn", "agent_monitor", "app_entrypoint"] | None = None,
):
    return {
        "items": await container.schedules.list(
            context,
            include_disabled=include_disabled,
            kind=kind,
        )
    }


@router.get("/runs")
async def list_schedule_runs(
    context: ContextDep,
    container: ContainerDep,
    schedule_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
):
    return {
        "items": await container.schedules.list_runs(context, schedule_id=schedule_id, limit=limit)
    }


@router.post("", status_code=201)
async def create_schedule(
    body: CreateScheduleRequest, context: ContextDep, container: ContainerDep
):
    return await container.schedules.create(context, body)


@router.post("/{schedule_id}/runs", status_code=202)
async def run_schedule_now(
    schedule_id: str, context: ContextDep, container: ContainerDep
):
    return await container.schedules.run_now(context, schedule_id)


@router.get("/{schedule_id}/execution-summary")
async def get_schedule_execution_summary(
    schedule_id: str, context: ContextDep, container: ContainerDep
):
    return await container.schedules.execution_summary(context, schedule_id)


@router.post("/{schedule_id}/resume")
async def resume_schedule(
    schedule_id: str,
    body: ResumeScheduleRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.schedules.resume(context, schedule_id, body)


@router.get("/{schedule_id}/monitor-scratch")
async def get_monitor_scratch(
    schedule_id: str, context: ContextDep, container: ContainerDep
):
    return await container.schedules.monitor_scratch(context, schedule_id)


@router.put("/{schedule_id}/monitor-scratch")
async def update_monitor_scratch(
    schedule_id: str,
    body: UpdateMonitorScratchRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.schedules.update_monitor_scratch(context, schedule_id, body)


@router.get("/{schedule_id}/monitor-scratch/revisions")
async def list_monitor_scratch_revisions(
    schedule_id: str,
    context: ContextDep,
    container: ContainerDep,
    limit: int = Query(default=50, ge=1, le=200),
):
    return {
        "items": await container.schedules.monitor_scratch_revisions(
            context, schedule_id, limit=limit
        )
    }


@router.patch("/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    body: UpdateScheduleRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.schedules.update(context, schedule_id, body)


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str, context: ContextDep, container: ContainerDep):
    await container.schedules.delete(context, schedule_id)
    return Response(status_code=204)
