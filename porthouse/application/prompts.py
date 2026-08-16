"""Prompt authoring, evidence gating, and immutable Agent bindings."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from porthouse.application.evals import require_release_gate


class PromptService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def list(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_prompts)

    async def get(self, prompt_id: str) -> dict[str, Any]:
        value = await asyncio.to_thread(self.store.get_prompt, prompt_id)
        if value is None:
            raise ValueError("Prompt not found")
        value["revisions"] = await asyncio.to_thread(
            self.store.list_prompt_revisions, prompt_id
        )
        return value

    async def save_draft(self, value: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.store.save_prompt_draft, value, actor_id=actor_id)

    async def validate(
        self, prompt_id: str, version: int, *, actor_id: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.validate_prompt_revision, prompt_id, version, actor_id=actor_id
        )

    async def publish(
        self, prompt_id: str, version: int, *, actor_id: str
    ) -> dict[str, Any]:
        revision = await asyncio.to_thread(
            self.store.get_prompt_revision_by_version, prompt_id, version
        )
        if revision is None:
            raise ValueError("Prompt revision not found")
        await require_release_gate(
            self.store,
            target_type="prompt",
            target_id=prompt_id,
            target_revision_id=revision["revision_id"],
            purpose="publish_prompt_revision",
            actor_id=actor_id,
        )
        return await asyncio.to_thread(
            self.store.publish_prompt_revision, prompt_id, version, actor_id=actor_id
        )

    async def bind(
        self, value: dict[str, Any], *, actor_id: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.bind_prompt_revision,
            binding_id=str(value.get("binding_id") or f"promptbind_{uuid4().hex}"),
            target_type=str(value.get("target_type") or "agent"),
            target_id=str(value.get("target_id") or ""),
            target_revision_id=str(value.get("target_revision_id") or ""),
            prompt_revision_id=str(value.get("prompt_revision_id") or ""),
            purpose=str(value.get("purpose") or "system_instruction"),
            position=int(value.get("position") or 100),
            enabled=bool(value.get("enabled", True)),
            actor_id=actor_id,
        )
