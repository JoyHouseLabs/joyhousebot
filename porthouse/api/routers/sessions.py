"""Conversation session HTTP endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query

from porthouse.api.dependencies import ContainerDep, ContextDep

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(
    context: ContextDep,
    container: ContainerDep,
    limit: int = Query(default=200, ge=1, le=500),
):
    return {"items": await container.sessions.list(context, limit=limit)}


@router.get("/{agent_id}/{session_id}/history")
async def session_history(
    agent_id: str,
    session_id: str,
    context: ContextDep,
    container: ContainerDep,
    limit: int = Query(default=100, ge=1, le=500),
):
    return {
        "items": await container.sessions.history(
            context, agent_id=agent_id, session_id=session_id, limit=limit
        )
    }


@router.delete("/{agent_id}/{session_id}")
async def delete_session(
    agent_id: str, session_id: str, context: ContextDep, container: ContainerDep
):
    return {
        "deleted": await container.sessions.delete(
            context, agent_id=agent_id, session_id=session_id
        )
    }
