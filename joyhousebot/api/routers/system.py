"""Health, identity, agent catalog, and usage endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from joyhousebot.api.dependencies import ContainerDep, ContextDep, PlatformAdminDep, PrincipalDep
from joyhousebot.application.presenters import public_capability_definition

router = APIRouter(tags=["system"])


@router.get("/me")
async def me(principal: PrincipalDep):
    return {
        "subject": principal.subject,
        "user_id": principal.user_id,
        "actor_user_id": principal.actor_user_id or principal.user_id,
        "impersonating": bool(
            principal.actor_user_id and principal.actor_user_id != principal.user_id
        ),
        "role": principal.role,
        "permissions": list(principal.permissions),
        "token_scopes": list(principal.token_scopes),
        "token_type": principal.token_type,
        "is_admin": principal.can("platform.read"),
    }


@router.get("/system/health")
async def system_health(context: ContextDep, container: ContainerDep):
    """Detailed store healthcheck; authenticated counterpart of public /readyz."""
    return await asyncio.to_thread(container.store.healthcheck)


@router.get("/system/metrics")
async def system_metrics(context: ContextDep, container: ContainerDep, principal: PlatformAdminDep):
    """Low-cardinality runtime counters suitable for control-plane polling."""
    return await asyncio.to_thread(container.store.operational_metrics)


@router.get("/agents")
async def list_agents(context: ContextDep, container: ContainerDep):
    profiles, active_models, workers, provider_configurations = await asyncio.gather(
        asyncio.to_thread(container.store.list_agent_profiles),
        asyncio.to_thread(container.store.list_active_models),
        asyncio.to_thread(container.store.list_runtime_workers, limit=500),
        asyncio.to_thread(container.store.list_active_model_provider_configurations),
    )
    active_models_by_id = {
        str(item.get("model_id") or ""): str(item.get("provider_id") or "")
        for item in active_models
        if item.get("enabled", True) and str(item.get("kind") or "llm") == "llm"
    }
    provider_extension_ids = {
        provider_id: str(configuration.get("extension_id") or "")
        for provider_id, configuration in provider_configurations.items()
    }
    provider_extension_ids_to_check = sorted(
        extension_id for extension_id in set(provider_extension_ids.values()) if extension_id
    )
    provider_workers = await asyncio.gather(
        *(
            asyncio.to_thread(container.store.list_extension_workers, extension_id)
            for extension_id in provider_extension_ids_to_check
        )
    )
    provider_worker_ready = {
        extension_id: any(
            item.get("healthy")
            and item.get("extension") is not None
            and item.get("execution_eligible")
            for item in loaded_workers
        )
        for extension_id, loaded_workers in zip(
            provider_extension_ids_to_check, provider_workers, strict=True
        )
    }
    has_execution_worker = any(
        item.get("healthy") and bool(dict(item.get("capabilities") or {}).get("agent"))
        for item in workers
    )
    rows = [
        _public_agent(
            profile,
            active_models_by_id,
            has_execution_worker,
            provider_extension_ids,
            provider_worker_ready,
        )
        for profile in profiles
    ]
    return {"items": rows}


def _public_agent(
    profile,
    active_models_by_id: dict[str, str],
    has_execution_worker: bool,
    provider_extension_ids: dict[str, str],
    provider_worker_ready: dict[str, bool],
):
    model = str(profile.revision.model_policy.get("primary") or "")
    blockers: list[str] = []
    if not model or model == "unconfigured/model":
        blockers.append("Agent 尚未选择已发布模型")
    elif model not in active_models_by_id:
        blockers.append(f"Agent 模型不在已生效目录中：{model}")
    else:
        provider_id = active_models_by_id[model]
        extension_id = provider_extension_ids.get(provider_id, "")
        if not extension_id or not provider_worker_ready.get(extension_id, False):
            blockers.append(f"模型 Provider 未由健康 Worker 加载：{provider_id}")
    if not has_execution_worker:
        blockers.append("没有健康的 Agent Worker")
    return {
        "id": profile.definition.agent_id,
        "name": profile.definition.name,
        "description": profile.definition.description,
        "role": profile.definition.role,
        "is_default": profile.definition.is_default,
        "revision_id": profile.revision.revision_id,
        "model": model,
        "execution_ready": not blockers,
        "execution_blockers": blockers,
    }


@router.get("/capabilities")
async def list_capabilities(context: ContextDep, container: ContainerDep):
    rows = await asyncio.to_thread(container.store.list_capability_definitions)
    return {"items": [public_capability_definition(row) for row in rows]}


@router.get("/scenarios")
async def list_scenarios(context: ContextDep, container: ContainerDep):
    rows = await asyncio.to_thread(container.store.list_scenario_versions)
    return {"items": [row.to_dict() for row in rows]}


@router.get("/usage")
async def usage(context: ContextDep, container: ContainerDep):
    return await asyncio.to_thread(container.store.get_user_model_usage, context.user_id)
