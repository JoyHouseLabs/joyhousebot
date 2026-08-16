"""Webhook trigger management and public event delivery."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response

from porthouse.api.dependencies import ContainerDep, ContextDep
from porthouse.api.schemas import (
    CreateEventTriggerRequest,
    ReceiveWebhookEventRequest,
    UpdateEventTriggerRequest,
)

router = APIRouter(tags=["automation"])


@router.get("/event-triggers")
async def list_event_triggers(context: ContextDep, container: ContainerDep):
    return {"items": await container.event_triggers.list(context)}


@router.post("/event-triggers", status_code=201)
async def create_event_trigger(
    body: CreateEventTriggerRequest, context: ContextDep, container: ContainerDep
):
    return await container.event_triggers.create(context, body.model_dump())


@router.patch("/event-triggers/{trigger_id}")
async def update_event_trigger(
    trigger_id: str,
    body: UpdateEventTriggerRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.event_triggers.update(
        context, trigger_id, body.model_dump(exclude_unset=True)
    )


@router.post("/event-triggers/{trigger_id}/rotate-secret")
async def rotate_event_trigger_secret(
    trigger_id: str, context: ContextDep, container: ContainerDep
):
    return await container.event_triggers.rotate_secret(context, trigger_id)


@router.delete("/event-triggers/{trigger_id}", status_code=204)
async def delete_event_trigger(
    trigger_id: str, context: ContextDep, container: ContainerDep
):
    await container.event_triggers.delete(context, trigger_id)
    return Response(status_code=204)


@router.get("/event-trigger-deliveries")
async def list_event_trigger_deliveries(
    context: ContextDep,
    container: ContainerDep,
    trigger_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    return {
        "items": await container.event_triggers.list_deliveries(
            context, trigger_id=trigger_id, limit=limit
        )
    }


@router.post("/hooks/{trigger_id}", status_code=202)
async def receive_webhook_event(
    trigger_id: str,
    body: ReceiveWebhookEventRequest,
    request: Request,
    container: ContainerDep,
    webhook_secret: Annotated[
        str | None, Header(alias="X-Porthouse-Webhook-Secret")
    ] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    return await container.event_triggers.receive(
        trigger_id,
        secret=str(webhook_secret or ""),
        idempotency_key=str(idempotency_key or ""),
        event_type=body.event_type,
        payload=body.payload,
        request_id=str(request.state.request_id),
        tracker_id=str(request.state.tracker_id),
    )
