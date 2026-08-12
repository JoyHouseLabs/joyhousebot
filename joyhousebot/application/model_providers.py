"""Use cases for the model provider and model catalog control plane."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.domain.model_providers import validate_agent_model_policy


class ModelProviderService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def list_providers(self) -> list[dict[str, Any]]:
        values = await asyncio.to_thread(self.store.list_model_providers)
        return [await self._enrich(item) for item in values]

    async def get_provider(self, provider_id: str) -> dict[str, Any] | None:
        value = await asyncio.to_thread(self.store.get_model_provider, provider_id)
        return await self._enrich(value) if value is not None else None

    async def list_models(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_active_models)

    async def save_revision(
        self,
        provider_id: str,
        *,
        name: str,
        description: str,
        configuration: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.save_model_provider_revision,
            provider_id,
            name=name,
            description=description,
            configuration=configuration,
            actor_id=actor_id,
        )

    async def publish_revision(
        self,
        provider_id: str,
        revision_id: str,
        *,
        actor_id: str,
        rollout_policy: dict[str, Any],
    ) -> dict[str, Any]:
        revision = await asyncio.to_thread(
            self.store.get_model_provider_revision, provider_id, revision_id
        )
        if revision is None:
            raise ValueError("model provider revision not found")
        await self._validate_active_agent_dependencies(
            provider_id, dict(revision.get("configuration") or {})
        )
        rollout_id = await asyncio.to_thread(
            self.store.stage_model_provider_revision,
            provider_id,
            revision_id,
            actor_id=actor_id,
            **rollout_policy,
        )
        return {**revision, "status": "staged", "rollout_id": rollout_id}

    async def _validate_active_agent_dependencies(
        self, provider_id: str, target_configuration: dict[str, Any]
    ) -> None:
        configurations = await asyncio.to_thread(
            self.store.list_active_model_provider_configurations
        )
        first_provider_bootstrap = not configurations
        configurations[provider_id] = target_configuration
        prospective_models = [
            {**dict(model), "provider_id": candidate_provider_id}
            for candidate_provider_id, configuration in configurations.items()
            if bool(configuration.get("enabled", True))
            for model in configuration.get("models") or ()
            if bool(model.get("enabled", True))
        ]
        definitions = await asyncio.to_thread(
            self.store.list_agent_definitions, active_only=True
        )
        for definition in definitions:
            if not definition.current_revision_id:
                continue
            agent_revision = await asyncio.to_thread(
                self.store.get_agent_revision, definition.current_revision_id
            )
            if agent_revision is None:
                continue
            if first_provider_bootstrap and _is_unconfigured_bootstrap_agent(
                agent_revision.model_policy
            ):
                # A genuinely empty Runtime seeds one inert Agent whose exact
                # model is the fail-closed ``unconfigured/model`` sentinel.
                # The first Provider must be allowed to become active before
                # an operator can publish the Agent revision that selects one
                # of its models.  Skipping only this sentinel during the first
                # Provider rollout breaks that bootstrap cycle without making
                # the placeholder executable or weakening later catalog
                # compatibility checks.
                continue
            try:
                validate_agent_model_policy(agent_revision.model_policy, prospective_models)
            except ValueError as exc:
                raise ValueError(
                    f"model provider revision would break active Agent {definition.agent_id}: {exc}"
                ) from exc

    async def _enrich(self, value: dict[str, Any]) -> dict[str, Any]:
        revision = value.get("current_revision") or value.get("latest_revision")
        configuration = dict((revision or {}).get("configuration") or {})
        extension_id = str(configuration.get("extension_id") or "")
        extension_release = (
            await asyncio.to_thread(self.store.get_active_plugin_release, extension_id)
            if extension_id
            else None
        )
        workers = (
            await asyncio.to_thread(self.store.list_plugin_workers, extension_id)
            if extension_id
            else []
        )
        eligible_workers = [
            item
            for item in workers
            if item.get("healthy") and item.get("plugin") is not None
        ]
        loaded_workers = sum(
            1 for item in eligible_workers if item.get("execution_eligible")
        )
        rollouts = await asyncio.to_thread(
            self.store.list_configuration_rollouts, limit=1000
        )
        latest_rollout = next(
            (
                item
                for item in rollouts
                if item.aggregate_type == "model_provider"
                and item.aggregate_id == value["provider_id"]
            ),
            None,
        )
        targets = (
            await asyncio.to_thread(
                self.store.list_configuration_rollout_targets,
                latest_rollout.rollout_id,
            )
            if latest_rollout is not None
            else []
        )
        blockers: list[str] = []
        if extension_release is None:
            blockers.append("模型 Provider 扩展尚未发布生效")
        if value.get("current_revision") is None:
            blockers.append("Provider 尚无已生效 Revision")
        if not loaded_workers:
            blockers.append("没有健康 Agent Worker 加载当前 Provider 扩展")
        return {
            **value,
            "model_count": len(configuration.get("models") or ()),
            "extension_release": extension_release,
            "worker_summary": {
                "total": len(eligible_workers),
                "loaded": loaded_workers,
            },
            "latest_rollout": (
                {**latest_rollout.to_dict(), "targets": targets}
                if latest_rollout is not None
                else None
            ),
            "execution_ready": not blockers,
            "execution_blockers": blockers,
        }


def _is_unconfigured_bootstrap_agent(model_policy: dict[str, Any]) -> bool:
    fallbacks = [
        str(item).strip()
        for item in model_policy.get("fallbacks") or ()
        if str(item).strip()
    ]
    return (
        str(model_policy.get("primary") or "").strip() == "unconfigured/model"
        and not fallbacks
    )


__all__ = ["ModelProviderService"]
