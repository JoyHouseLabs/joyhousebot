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
async def system_metrics(
    context: ContextDep, container: ContainerDep, principal: PlatformAdminDep
):
    """Low-cardinality runtime counters suitable for control-plane polling."""
    return await asyncio.to_thread(container.store.operational_metrics)


@router.get("/agents")
async def list_agents(context: ContextDep, container: ContainerDep):
    profiles = await asyncio.to_thread(container.store.list_agent_profiles)
    rows = [
        {
            "id": profile.definition.agent_id,
            "name": profile.definition.name,
            "description": profile.definition.description,
            "role": profile.definition.role,
            "is_default": profile.definition.is_default,
            "revision_id": profile.revision.revision_id,
            "model": profile.revision.model_policy["primary"],
        }
        for profile in profiles
    ]
    return {"items": rows}


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
    records = await asyncio.to_thread(
        container.store.list_runtime_runs, user_id=context.user_id, limit=1000
    )
    input_tokens = output_tokens = 0
    cost_usd = 0.0
    for row in records:
        values = (row.result or {}).get("usage") or {}
        input_tokens += int(values.get("input_tokens") or 0)
        output_tokens += int(values.get("output_tokens") or 0)
        cost_usd += float(values.get("cost_usd") or 0.0)
    return {
        "runs": len(records),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": cost_usd,
    }
