"""Token-authenticated delivery endpoint for Graph external events."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header

from joyhousebot.api.dependencies import ContainerDep
from joyhousebot.api.schemas import ReceiveGraphEventRequest

router = APIRouter(prefix="/run-events", tags=["run-events"])


@router.post("/{wait_id}")
async def receive_graph_event(
    wait_id: str,
    body: ReceiveGraphEventRequest,
    container: ContainerDep,
    event_token: Annotated[str | None, Header(alias="X-JoyHouseBot-Event-Token")] = None,
):
    return await container.graph_events.receive(
        wait_id,
        token=str(event_token or ""),
        event_type=body.event_type,
        payload=body.payload,
    )
