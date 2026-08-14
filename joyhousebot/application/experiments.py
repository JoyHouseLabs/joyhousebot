"""Control-plane use cases for safe online Agent-revision experiments."""

from __future__ import annotations

import asyncio
from typing import Any


class ExperimentService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def list(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_experiments)

    async def get(self, experiment_id: str) -> dict[str, Any]:
        value = await asyncio.to_thread(self.store.get_experiment, experiment_id)
        if value is None:
            raise ValueError("experiment not found")
        return value

    async def save_draft(self, value: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.save_experiment_draft, value, actor_id=actor_id
        )

    async def start(self, experiment_id: str, *, actor_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.store.start_experiment, experiment_id, actor_id=actor_id)

    async def set_status(
        self, experiment_id: str, *, status: str, actor_id: str, reason: str = ""
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.set_experiment_status,
            experiment_id,
            status=status,
            actor_id=actor_id,
            reason=reason,
        )

    async def summary(self, experiment_id: str, *, enforce_guardrails: bool = True) -> dict[str, Any]:
        if enforce_guardrails:
            return await asyncio.to_thread(
                self.store.enforce_experiment_guardrails, experiment_id
            )
        return {"paused": False, "summary": await asyncio.to_thread(self.store.experiment_summary, experiment_id)}
