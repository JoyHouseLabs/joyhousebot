"""Derived action view for owner-facing Runtime input and approvals."""

from __future__ import annotations

from fastapi import APIRouter, Query

from porthouse.api.dependencies import ContainerDep, ContextDep

router = APIRouter(prefix="/action-items", tags=["action-items"])


@router.get("")
async def list_action_items(
    context: ContextDep,
    container: ContainerDep,
    limit: int = Query(default=100, ge=1, le=200),
):
    """List current human actions; source tables remain authoritative."""
    return {"items": await container.action_items.list(context, limit=limit)}
