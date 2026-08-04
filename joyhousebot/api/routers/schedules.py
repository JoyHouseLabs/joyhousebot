"""User schedule HTTP endpoints."""

from fastapi import APIRouter, Query, Response

from joyhousebot.api.dependencies import ContainerDep, ContextDep
from joyhousebot.api.schemas import CreateScheduleRequest, UpdateScheduleRequest

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("")
async def list_schedules(
    context: ContextDep,
    container: ContainerDep,
    include_disabled: bool = True,
):
    return {"items": await container.schedules.list(context, include_disabled=include_disabled)}


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
