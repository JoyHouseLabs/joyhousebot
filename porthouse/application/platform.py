"""Application service for control-plane catalogs and rollouts."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from jsonschema import Draft202012Validator, SchemaError
from loguru import logger

from porthouse.application.evals import require_release_gate
from porthouse.domain.agents import (
    AgentDefinition,
    AgentRevision,
    PluginReleaseRequirement,
)
from porthouse.domain.capabilities import (
    CapabilityDefinition,
    CapabilityRef,
    resolve_capability_policy,
)
from porthouse.domain.model_providers import validate_agent_model_policy
from porthouse.utils.permissions import missing_permissions


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
        definitions = await asyncio.to_thread(self.store.list_capability_definitions)
        policy = resolve_capability_policy(
            revision.capability_policy,
            definitions,
            strict=True,
        )
        self._validate_retrieval_rerank_policy(revision, definitions, policy)
        by_id = {
            str(item.get("ref", {}).get("capability_id") or ""): item
            for item in definitions
        }
        requirements = {
            item.plugin_id: item for item in revision.plugin_requirements
        }
        for capability_id in policy["allowed"]:
            item = by_id.get(capability_id)
            if item is None:
                continue
            reference = CapabilityRef.from_dict(dict(item.get("ref") or {}))
            requirement = PluginReleaseRequirement(
                plugin_id=reference.plugin_id,
                version=reference.plugin_version,
                build_digest=reference.plugin_build_digest,
            )
            existing = requirements.get(requirement.plugin_id)
            if existing is not None and existing != requirement:
                raise ValueError(
                    "Agent capabilities require conflicting plugin releases: "
                    f"{requirement.plugin_id}"
                )
            requirements[requirement.plugin_id] = requirement
        revision = replace(
            revision,
            plugin_requirements=tuple(
                requirements[key] for key in sorted(requirements)
            ),
        )
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
        revision = await asyncio.to_thread(self.store.get_agent_revision, revision_id)
        if revision is None or revision.agent_id != agent_id:
            raise ValueError("Agent revision not found")
        definitions = await asyncio.to_thread(self.store.list_capability_definitions)
        policy = resolve_capability_policy(
            revision.capability_policy,
            definitions,
            strict=True,
        )
        self._validate_retrieval_rerank_policy(revision, definitions, policy)
        resolved = set(policy["resolved"])
        required_permissions = {
            str(permission).strip()
            for item in definitions
            if str(dict(item.get("ref") or {}).get("capability_id") or "") in resolved
            for permission in (item.get("permissions") or ())
            if str(permission).strip()
        }
        grants = policy.get("permissions") or ()
        if not isinstance(grants, (list, tuple, set, frozenset)):
            raise ValueError("capability_policy.permissions must be an array")
        missing = missing_permissions(grants, required_permissions)
        if missing:
            raise ValueError(
                "Agent capability policy is missing execution permissions: "
                + ", ".join(missing)
            )
        active_models = await asyncio.to_thread(self.store.list_active_models)
        if active_models:
            validate_agent_model_policy(revision.model_policy, active_models)
        skill_bindings = await asyncio.to_thread(
            self.store.list_agent_skill_bindings, revision_id
        )
        for binding in skill_bindings:
            skill = await asyncio.to_thread(
                self.store.get_published_skill,
                str(binding["skill_id"]),
                str(binding["skill_version"]),
            )
            if skill is None:
                raise ValueError(
                    "Agent references an unavailable Skill version: "
                    f"{binding['skill_id']}@{binding['skill_version']}"
                )
            if str(skill["content_sha256"]) != str(binding["content_sha256"]):
                raise ValueError(
                    "Agent Skill content digest does not match its pinned version: "
                    f"{binding['skill_id']}@{binding['skill_version']}"
                )
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

    def _validate_retrieval_rerank_policy(
        self,
        revision: AgentRevision,
        definitions: list[dict[str, Any]],
        capability_policy: dict[str, Any],
    ) -> None:
        """Validate the frozen Rerank reference before an Agent is saved.

        Retrieval is composed by Core, so it cannot accept a model-supplied
        capability choice.  An enabled policy must pin the active published
        Rerank capability and must be covered by the Agent's resolved
        capability policy; otherwise it would fail at the Dispatcher boundary
        only after a Run has already started.
        """
        retrieval = dict(revision.memory_policy.get("retrieval") or {})
        raw = retrieval.get("rerank")
        if raw is None:
            return
        if not isinstance(raw, dict):
            raise ValueError("memory_policy.retrieval.rerank must be an object")
        if not raw.get("enabled"):
            return
        capability_id = str(raw.get("capability_id") or "").strip()
        version = str(raw.get("version") or "").strip()
        if capability_id != "retrieval.rerank" or not version:
            raise ValueError(
                "enabled retrieval rerank must pin retrieval.rerank and an exact version"
            )
        try:
            candidate_limit = int(raw.get("candidate_limit") or 20)
            top_k = int(raw.get("top_k") or candidate_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("rerank candidate_limit and top_k must be integers") from exc
        if not 1 <= candidate_limit <= 50 or not 1 <= top_k <= candidate_limit:
            raise ValueError("rerank candidate_limit/top_k must be within 1..50")
        if str(raw.get("failure_mode") or "fallback") not in {
            "fallback",
            "fail_closed",
        }:
            raise ValueError("rerank failure_mode must be fallback or fail_closed")
        definition = self.store.get_capability_definition(capability_id, version)
        if definition is None:
            raise ValueError("configured Rerank capability is not published and active")
        if not any(
            str(dict(item.get("ref") or {}).get("capability_id") or "")
            == capability_id
            for item in definitions
        ):
            raise ValueError("configured Rerank capability is not available to this Agent")
        if capability_id not in set(capability_policy.get("resolved") or ()):
            raise ValueError(
                "Agent capability policy must authorize retrieval.rerank when rerank is enabled"
            )

    async def bind_agent_skill(self, **kwargs: Any) -> None:
        await asyncio.to_thread(self.store.bind_agent_skill, **kwargs)

    async def unbind_agent_skill(self, **kwargs: Any) -> bool:
        return await asyncio.to_thread(self.store.unbind_agent_skill, **kwargs)

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
        component_rollouts = await self.publish_plugin_capabilities(
            plugin_id,
            version,
            actor_id=actor_id,
            rollout_policy=rollout_policy,
        )
        rollout_id = await asyncio.to_thread(
            self.store.stage_plugin_release,
            plugin_id,
            version,
            actor_id=actor_id,
            **dict(rollout_policy or {}),
        )
        return {
            **release,
            "status": "staged",
            "rollout_id": rollout_id,
            "component_rollouts": component_rollouts,
        }

    async def publish_plugin_capabilities(
        self,
        plugin_id: str,
        plugin_version: str,
        *,
        actor_id: str,
        rollout_policy: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """Stage exact discovered Capability builds owned by one extension release."""
        components = await asyncio.to_thread(
            self.store.list_plugin_components, plugin_id, plugin_version
        )
        definitions: list[CapabilityDefinition] = []
        seen: set[tuple[str, str]] = set()
        for component in components:
            if component.get("component_type") not in {"tool", "connector"}:
                continue
            capability_id = str(component.get("reference_id") or "")
            capability_version = str(component.get("reference_version") or "")
            key = (capability_id, capability_version)
            if not all(key) or key in seen:
                continue
            seen.add(key)
            stored = await asyncio.to_thread(
                self.store.get_capability_release_definition,
                capability_id,
                capability_version,
            )
            if stored is None:
                continue
            definition = CapabilityDefinition.from_dict(stored)
            if (
                definition.ref.plugin_id != plugin_id
                or definition.ref.plugin_version != plugin_version
            ):
                continue
            published = await asyncio.to_thread(
                self.store.get_capability_definition,
                capability_id,
                capability_version,
            )
            if published is None:
                definitions.append(definition)

        for definition in definitions:
            await require_release_gate(
                self.store,
                target_type="capability",
                target_id=definition.ref.capability_id,
                target_revision_id=definition.ref.version,
                purpose="publish_capability_version",
                actor_id=actor_id,
            )
        for definition in definitions:
            await asyncio.to_thread(
                self.store.stage_capability_release,
                definition,
                actor_id=actor_id,
                **dict(rollout_policy or {}),
            )
        return [
            {
                "capability_id": item.ref.capability_id,
                "version": item.ref.version,
                "status": "staged",
            }
            for item in definitions
        ]

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

    async def list_rollouts(
        self, *, limit: int, aggregate_type: str | None = None
    ) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            self.store.list_configuration_rollouts,
            limit=limit,
            aggregate_type=aggregate_type,
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
