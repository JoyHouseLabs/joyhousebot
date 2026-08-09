"""Application service for control-plane catalogs and rollouts."""

from __future__ import annotations

import asyncio
from typing import Any

from jsonschema import Draft202012Validator, SchemaError
from loguru import logger

from joyhousebot.application.evals import require_release_gate
from joyhousebot.domain.agents import AgentDefinition, AgentRevision
from joyhousebot.domain.capabilities import CapabilityDefinition


class PlatformService:
    def __init__(self, store: Any, monitor_reconciler: Any | None = None) -> None:
        self.store = store
        self.monitor_reconciler = monitor_reconciler

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
        self,
        agent_id: str,
        revision_id: str,
        *,
        actor_id: str,
        rollout_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await require_release_gate(
            self.store,
            target_type="agent",
            target_id=agent_id,
            target_revision_id=revision_id,
            purpose="publish_agent_revision",
            actor_id=actor_id,
        )
        profile = await asyncio.to_thread(
            self.store.publish_agent_revision,
            agent_id,
            revision_id,
            actor_id=actor_id,
            **dict(rollout_policy or {}),
        )
        active_profile = await asyncio.to_thread(self.store.get_agent_profile, agent_id)
        if (
            self.monitor_reconciler is not None
            and active_profile is not None
            and active_profile.revision.revision_id == revision_id
        ):
            try:
                await asyncio.to_thread(self.monitor_reconciler, active_profile)
            except Exception:
                # Publication already committed. Existing user schedules will
                # be repaired on their next Run, so do not report a false
                # transactional failure to the administrator.
                logger.exception("Managed Agent Monitor publish reconciliation failed")
        return profile.to_dict()

    async def bind_agent_skill(self, **kwargs: Any) -> None:
        await asyncio.to_thread(self.store.bind_agent_skill, **kwargs)

    async def list_capabilities(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_capability_definitions)

    async def publish_capability(
        self,
        definition: CapabilityDefinition,
        *,
        actor_id: str,
        rollout_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await require_release_gate(
            self.store,
            target_type="capability",
            target_id=definition.ref.capability_id,
            target_revision_id=definition.ref.version,
            purpose="publish_capability_version",
            actor_id=actor_id,
        )
        try:
            for schema in (
                definition.input_schema,
                definition.output_schema,
                definition.configuration_schema,
            ):
                if schema:
                    Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ValueError(f"invalid capability JSON Schema: {exc.message}") from exc
        await asyncio.to_thread(
            self.store.stage_capability_release,
            definition,
            actor_id=actor_id,
            **dict(rollout_policy or {}),
        )
        return definition.to_dict()

    async def publish_plugin_release(
        self,
        plugin_id: str,
        version: str,
        *,
        actor_id: str,
        rollout_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        release = await asyncio.to_thread(
            self.store.get_plugin_release, plugin_id, version
        )
        if release is None:
            raise ValueError("plugin release has not been discovered")
        rollout_id = await asyncio.to_thread(
            self.store.stage_plugin_release,
            plugin_id,
            version,
            actor_id=actor_id,
            **dict(rollout_policy or {}),
        )
        return {**release, "status": "staged", "rollout_id": rollout_id}

    async def approve_rollout(self, rollout_id: str, *, actor_id: str) -> bool:
        return await asyncio.to_thread(
            self.store.approve_configuration_rollout, rollout_id, actor_id=actor_id
        )

    async def cancel_rollout(self, rollout_id: str, *, actor_id: str) -> bool:
        return await asyncio.to_thread(
            self.store.cancel_configuration_rollout, rollout_id, actor_id=actor_id
        )

    async def retry_rollout(self, rollout_id: str, *, actor_id: str) -> bool:
        return await asyncio.to_thread(
            self.store.retry_configuration_rollout, rollout_id, actor_id=actor_id
        )

    async def rollback_rollout(self, rollout_id: str, *, actor_id: str) -> bool:
        return await asyncio.to_thread(
            self.store.rollback_configuration_rollout, rollout_id, actor_id=actor_id
        )

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
