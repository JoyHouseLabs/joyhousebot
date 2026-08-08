"""Authenticated owner API for governed Memory updates."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from joyhousebot.api.dependencies import ContainerDep, ContextDep
from joyhousebot.api.schemas import ResolveMemoryCandidateRequest
from joyhousebot.application.presenters import record_dict

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/candidates")
async def list_memory_candidates(
    context: ContextDep,
    container: ContainerDep,
    status: Literal[
        "pending", "merged", "rejected", "expired", "conflicted", "all"
    ] = "pending",
    limit: int = Query(default=100, ge=1, le=500),
):
    rows = await container.memory_candidates.list(
        context, status=None if status == "all" else status, limit=limit
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
