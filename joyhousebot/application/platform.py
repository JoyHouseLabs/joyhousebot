"""Application service for control-plane catalogs and rollouts."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.domain.agents import AgentDefinition, AgentRevision
from joyhousebot.domain.capabilities import CapabilityDefinition


class PlatformService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def list_workers(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_runtime_workers, limit=500)

    async def list_agents(self) -> list[dict[str, Any]]:
        definitions = await asyncio.to_thread(
            self.store.list_agent_definitions, active_only=False
        )
        output = []
        for definition in definitions:
            revision = (
                await asyncio.to_thread(
                    self.store.get_agent_revision, definition.current_revision_id
                )
                if definition.current_revision_id
                else None
            )
            output.append(
                {
                    **definition.to_dict(),
                    "revision": revision.to_dict() if revision is not None else None,
                }
            )
        return output

    async def list_agent_revisions(self, agent_id: str) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(self.store.list_agent_revisions, agent_id)
        return [row.to_dict() for row in rows]

    async def list_agent_skill_bindings(
        self, agent_revision_id: str
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.store.list_agent_skill_bindings, agent_revision_id
        )

    async def save_agent_revision(
        self, definition: AgentDefinition, revision: AgentRevision
    ) -> dict[str, Any]:
        await asyncio.to_thread(self.store.save_agent_revision, definition, revision)
        stored = await asyncio.to_thread(self.store.get_agent_revision, revision.revision_id)
        assert stored is not None
        return stored.to_dict()

    async def publish_agent_revision(
        self, agent_id: str, revision_id: str, *, actor_id: str
    ) -> dict[str, Any]:
        profile = await asyncio.to_thread(
            self.store.publish_agent_revision,
            agent_id,
            revision_id,
            actor_id=actor_id,
        )
        return profile.to_dict()

    async def bind_agent_skill(self, **kwargs: Any) -> None:
        await asyncio.to_thread(self.store.bind_agent_skill, **kwargs)

    async def list_capabilities(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_capability_definitions)

    async def publish_capability(
        self, definition: CapabilityDefinition, *, actor_id: str
    ) -> dict[str, Any]:
        await asyncio.to_thread(
            self.store.publish_capability, definition, actor_id=actor_id
        )
        return definition.to_dict()

    async def list_rollouts(self, *, limit: int) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            self.store.list_configuration_rollouts, limit=limit
        )
        output = []
        for row in rows:
            targets = await asyncio.to_thread(
                self.store.list_configuration_rollout_targets, row.rollout_id
            )
            output.append({**row.to_dict(), "targets": targets})
        return output

    async def list_configuration_events(self, *, limit: int) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(self.store.list_configuration_events, limit=limit)
        return [row.to_dict() for row in rows]
