"""User-scoped conversation session queries."""

from __future__ import annotations

import asyncio
from typing import Any

from porthouse.application.context import RequestContext
from porthouse.application.errors import ConflictError, NotFoundError


class SessionService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def list(self, context: RequestContext, *, limit: int = 200) -> list[dict[str, Any]]:
        records = await asyncio.to_thread(
            self.store.list_runtime_runs, user_id=context.user_id, limit=min(limit * 10, 1000)
        )
        sessions: dict[tuple[str, str], dict[str, Any]] = {}
        for row in records:
            key = (row.agent_id, row.session_id)
            if key in sessions:
                continue
            sessions[key] = {
                "agent_id": row.agent_id,
                "session_id": row.session_id,
                "latest_run_id": row.run_id,
                "latest_status": row.status,
                "updated_at": row.updated_at,
            }
        return list(sessions.values())[:limit]

    async def history(
        self,
        context: RequestContext,
        *,
        agent_id: str,
        session_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        records = await asyncio.to_thread(
            self.store.list_runtime_runs,
            user_id=context.user_id,
            agent_id=agent_id,
            session_id=session_id,
            limit=limit,
        )
        if not records:
            raise NotFoundError("session not found")
        messages: list[dict[str, Any]] = []
        for row in reversed(records):
            messages.append({"role": "user", "content": row.prompt, "run_id": row.run_id})
            content = str((row.result or {}).get("content") or "")
            if content:
                messages.append({"role": "assistant", "content": content, "run_id": row.run_id})
        return messages

    async def delete(self, context: RequestContext, *, agent_id: str, session_id: str) -> int:
        try:
            return await asyncio.to_thread(
                self.store.delete_runtime_session,
                user_id=context.user_id,
                agent_id=agent_id,
                session_id=session_id,
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
