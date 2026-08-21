"""Owner-scoped use cases for governed Memory candidates."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.application.context import RequestContext
from joyhousebot.application.errors import ConflictError, NotFoundError


class MemoryCandidateService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def list(
        self,
        context: RequestContext,
        *,
        agent_id: str | None = None,
        status: str | None = "pending",
        limit: int = 100,
    ) -> list[Any]:
        return await asyncio.to_thread(
            self.store.list_memory_candidates,
            user_id=context.user_id,
            agent_id=agent_id,
            status=status,
            limit=limit,
        )

    async def resolve(
        self,
        context: RequestContext,
        candidate_id: str,
        *,
        resolution: str,
        note: str | None = None,
    ) -> Any:
        candidate, outcome = await asyncio.to_thread(
            self.store.resolve_memory_candidate,
            candidate_id=candidate_id,
            user_id=context.user_id,
            resolution=resolution,
            note=note,
            actor_id=context.principal.subject,
        )
        if candidate is None or outcome == "not_found":
            raise NotFoundError("Memory candidate not found")
        if outcome == "expired":
            raise ConflictError("Memory candidate has expired")
        if outcome == "document_conflict":
            raise ConflictError(
                "Memory changed after this candidate was proposed; reject it and create a new candidate"
            )
        if outcome == "terminal_conflict":
            raise ConflictError("Memory candidate is already resolved")
        return candidate
