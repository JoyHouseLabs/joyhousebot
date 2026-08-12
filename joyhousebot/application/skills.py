"""Application service for Skill authoring, validation and publication."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.application.evals import require_release_gate


class SkillService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def list(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_skills)

    async def get(self, skill_id: str) -> dict[str, Any]:
        value = await asyncio.to_thread(self.store.get_skill, skill_id)
        if value is None:
            raise ValueError("Skill not found")
        return value

    async def save_draft(
        self, value: dict[str, Any], *, actor_id: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.save_skill_draft, value, actor_id=actor_id
        )

    async def validate(
        self, skill_id: str, version: str, *, actor_id: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.validate_skill_version,
            skill_id,
            version,
            actor_id=actor_id,
        )

    async def publish(
        self,
        skill_id: str,
        version: str,
        *,
        actor_id: str,
        rollout_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await require_release_gate(
            self.store,
            target_type="skill",
            target_id=skill_id,
            target_revision_id=version,
            purpose="publish_skill_version",
            actor_id=actor_id,
        )
        rollout_id = await asyncio.to_thread(
            self.store.stage_skill_version,
            skill_id,
            version,
            actor_id=actor_id,
            **dict(rollout_policy or {}),
        )
        value = await asyncio.to_thread(self.store.get_skill_version, skill_id, version)
        assert value is not None
        return {**value, "rollout_id": rollout_id}

    async def set_status(
        self, skill_id: str, *, status: str, actor_id: str
    ) -> dict[str, Any]:
        found = await asyncio.to_thread(
            self.store.set_skill_status, skill_id, status=status, actor_id=actor_id
        )
        if not found:
            raise ValueError("Skill not found")
        return await self.get(skill_id)
