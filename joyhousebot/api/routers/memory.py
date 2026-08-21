"""Authenticated owner API for governed Memory updates."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from joyhousebot.api.dependencies import ContainerDep, ContextDep
from joyhousebot.api.schemas import ResolveMemoryCandidateRequest
from joyhousebot.application.presenters import record_dict

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/documents")
async def list_memory_documents(
    context: ContextDep,
    container: ContainerDep,
    agent_id: str = Query(min_length=1, max_length=200),
    layer: Literal["profile", "long_term", "episodic", "agent", "all"] = "all",
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=200, ge=1, le=500),
):
    """List this user's durable Memory documents for one Agent."""
    items, summary = await asyncio.gather(
        asyncio.to_thread(
            container.store.list_memory_documents,
            user_id=context.user_id,
            agent_id=agent_id,
            layer=None if layer == "all" else layer,
            search=search,
            limit=limit,
        ),
        asyncio.to_thread(
            container.store.summarize_memory_documents,
            user_id=context.user_id,
            agent_id=agent_id,
        ),
    )
    return {"items": items, "summary": summary}


@router.get("/documents/{document_path:path}")
async def get_memory_document(
    document_path: str,
    context: ContextDep,
    container: ContainerDep,
    agent_id: str = Query(min_length=1, max_length=200),
    scope_key: str = Query(min_length=1, max_length=500),
):
    """Read one full document after matching user, Agent, scope and path."""
    item = await asyncio.to_thread(
        container.store.get_memory_document,
        user_id=context.user_id,
        agent_id=agent_id,
        scope_key=scope_key,
        document_path=document_path,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Memory document not found")
    return item


@router.get("/candidates")
async def list_memory_candidates(
    context: ContextDep,
    container: ContainerDep,
    agent_id: str | None = Query(default=None, min_length=1, max_length=200),
    status: Literal[
        "pending", "merged", "rejected", "expired", "conflicted", "all"
    ] = "pending",
    limit: int = Query(default=100, ge=1, le=500),
):
    rows = await container.memory_candidates.list(
        context,
        agent_id=agent_id,
        status=None if status == "all" else status,
        limit=limit,
    )
    return {"items": [record_dict(row) for row in rows]}


@router.post("/candidates/{candidate_id}/resolve")
async def resolve_memory_candidate(
    candidate_id: str,
    body: ResolveMemoryCandidateRequest,
    context: ContextDep,
    container: ContainerDep,
):
    candidate = await container.memory_candidates.resolve(
        context,
        candidate_id,
        resolution=body.resolution,
        note=body.note,
    )
    return record_dict(candidate)
