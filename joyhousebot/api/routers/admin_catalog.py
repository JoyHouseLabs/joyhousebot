"""Versioned Agent/capability catalogs and rollout visibility."""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query

from joyhousebot.api.dependencies import (
    AgentsPublisherDep,
    AgentsReaderDep,
    AgentsWriterDep,
    AuditReaderDep,
    CapabilitiesPublisherDep,
    CapabilitiesReaderDep,
    ContainerDep,
    PlatformAdminDep,
    RolloutsReaderDep,
    RolloutsWriterDep,
    SettingsReaderDep,
    WorkersReaderDep,
)
from joyhousebot.api.schemas import (
    BindAgentSkillRequest,
    PublishCapabilityRequest,
    RolloutPolicyRequest,
    SaveAgentRevisionRequest,
    SaveCapabilityRuntimeSettingsRequest,
)
from joyhousebot.application.presenters import public_capability_definition
from joyhousebot.config.extensions import enabled_channel_ids
from joyhousebot.domain.agents import AgentDefinition, AgentRevision, PluginReleaseRequirement
from joyhousebot.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
)
from joyhousebot.domain.permissions import permission_catalog_response
from joyhousebot.extension_discovery import installed_extensions

router = APIRouter(prefix="/admin", tags=["platform-catalog"])


def _safe_endpoint(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return "configured"
    if not hostname:
        return "configured"
    return f"{parsed.scheme}://{hostname}{f':{port}' if port else ''}"


@router.get("/permissions")
async def permissions(principal: PlatformAdminDep):
    return permission_catalog_response()


@router.get("/workers")
async def workers(principal: WorkersReaderDep, container: ContainerDep):
    return {"items": await container.platform.list_workers()}


@router.get("/agents")
async def agents(principal: AgentsReaderDep, container: ContainerDep):
    return {"items": await container.platform.list_agents()}


@router.get("/agents/{agent_id}/revisions")
async def agent_revisions(agent_id: str, principal: AgentsReaderDep, container: ContainerDep):
    return {"items": await container.platform.list_agent_revisions(agent_id)}


@router.get("/agents/{agent_id}/revisions/{revision_id}/skills")
async def agent_skill_bindings(
    agent_id: str,
    revision_id: str,
    principal: AgentsReaderDep,
    container: ContainerDep,
):
    revision = await asyncio.to_thread(container.store.get_agent_revision, revision_id)
    if revision is None or revision.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Agent revision not found")
    return {"items": await container.platform.list_agent_skill_bindings(revision_id)}


@router.put("/agents/{agent_id}/revisions/{revision_id}")
async def save_agent_revision(
    agent_id: str,
    revision_id: str,
    body: SaveAgentRevisionRequest,
    principal: AgentsWriterDep,
    container: ContainerDep,
):
    if body.revision_id != revision_id:
        raise HTTPException(status_code=400, detail="body revision_id must match path")
    existing = next(
        (item for item in await container.platform.list_agents() if item["agent_id"] == agent_id),
        None,
    )
    definition = AgentDefinition(
        agent_id=agent_id,
        name=body.name,
        description=body.description,
        role=body.role,
        status=body.definition_status,
        is_default=bool(existing and existing.get("is_default")),
        current_revision_id=existing.get("current_revision_id") if existing else None,
    )
    revision = AgentRevision(
        revision_id=revision_id,
        agent_id=agent_id,
        version=body.version,
        persona=body.persona,
        instructions=body.instructions,
        model_policy=body.model_policy,
        planning_policy=body.planning_policy,
        capability_policy=body.capability_policy,
        memory_policy=body.memory_policy,
        output_policy=body.output_policy,
        monitor_policy=body.monitor_policy,
        plugin_requirements=tuple(
            PluginReleaseRequirement.from_dict(item) for item in body.plugin_requirements
        ),
        status="draft",
        created_by=principal.subject,
    )
    try:
        return await container.platform.save_agent_revision(definition, revision)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/agents/{agent_id}/revisions/{revision_id}/publish")
async def publish_agent_revision(
    agent_id: str,
    revision_id: str,
    principal: AgentsPublisherDep,
    container: ContainerDep,
    body: RolloutPolicyRequest | None = None,
):
    try:
        return await container.platform.publish_agent_revision(
            agent_id,
            revision_id,
            actor_id=principal.subject,
            rollout_policy=body.model_dump() if body is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/agents/{agent_id}/revisions/{revision_id}/skills")
async def bind_agent_skill(
    agent_id: str,
    revision_id: str,
    body: BindAgentSkillRequest,
    principal: AgentsWriterDep,
    container: ContainerDep,
):
    revision = await asyncio.to_thread(container.store.get_agent_revision, revision_id)
    if revision is None or revision.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Agent revision not found")
    try:
        await container.platform.bind_agent_skill(
            agent_revision_id=revision_id,
            skill_id=body.skill_id,
            skill_version=body.skill_version,
            activation_mode=body.activation_mode,
            priority=body.priority,
            configuration=body.configuration,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"saved": True}


@router.get("/capabilities")
async def capabilities(principal: CapabilitiesReaderDep, container: ContainerDep):
    rows = await container.platform.list_capabilities()
    return {"items": [public_capability_definition(item) for item in rows]}


@router.get("/capabilities/{capability_id}/runtime-settings")
async def capability_runtime_settings(
    capability_id: str, principal: CapabilitiesReaderDep, container: ContainerDep
):
    definition = await asyncio.to_thread(container.store.get_capability_definition, capability_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="capability not found")
    settings = await asyncio.to_thread(
        container.store.get_capability_runtime_settings, capability_id
    )
    schema = dict(definition.get("configuration_schema") or {})
    return {
        **settings,
        # An administrator can edit the effective values, but plugin-private
        # immutable configuration remains private. Only fields declared in
        # the runtime settings schema are projected here.
        "configuration": _effective_runtime_configuration(
            definition, settings["configuration"], schema
        ),
        "configuration_schema": schema,
    }


@router.put("/capabilities/{capability_id}/runtime-settings")
async def save_capability_runtime_settings(
    capability_id: str,
    body: SaveCapabilityRuntimeSettingsRequest,
    principal: CapabilitiesPublisherDep,
    container: ContainerDep,
):
    try:
        return await asyncio.to_thread(
            container.store.save_capability_runtime_settings,
            capability_id,
            enabled=body.enabled,
            configuration=body.configuration,
            actor_id=principal.subject,
        )
    except ValueError as exc:
        status = 404 if str(exc) == "capability not found" else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc


def _effective_runtime_configuration(definition: dict, overrides: dict, schema: dict) -> dict:
    base = dict(definition.get("configuration") or {})
    properties = schema.get("properties")
    if isinstance(properties, dict) and schema.get("additionalProperties") is False:
        base = {key: value for key, value in base.items() if key in properties}
    return {**base, **dict(overrides or {})}


@router.put("/capabilities/{capability_id}/versions/{version}")
async def publish_capability(
    capability_id: str,
    version: str,
    body: PublishCapabilityRequest,
    principal: CapabilitiesPublisherDep,
    container: ContainerDep,
):
    definition = CapabilityDefinition(
        ref=CapabilityRef(
            capability_id,
            version,
            CapabilityKind(body.kind),
            body.plugin_id,
            body.plugin_version,
            body.plugin_build_digest,
        ),
        name=body.name,
        description=body.description,
        input_schema=body.input_schema,
        output_schema=body.output_schema,
        adapter=body.adapter,
        tags=tuple(body.tags),
        execution_mode=body.execution_mode,
        expected_duration_seconds=body.expected_duration_seconds,
        timeout_seconds=body.timeout_seconds,
        idempotent=body.idempotent,
        retryable=body.retryable,
        side_effect=body.side_effect,
        compensation=(
            CapabilityRef.from_dict(body.compensation.model_dump())
            if body.compensation is not None
            else None
        ),
        invocation_concurrency=body.invocation_concurrency,
        max_concurrent_invocations=body.max_concurrent_invocations,
        supports_stream=body.supports_stream,
        permissions=tuple(body.permissions),
        data_classification=body.data_classification,
        connection_ids=tuple(body.connection_ids),
        cost_policy=body.cost_policy,
        configuration_schema=body.configuration_schema,
        configuration=body.configuration,
    )
    try:
        return await container.platform.publish_capability(
            definition,
            actor_id=principal.subject,
            rollout_policy=body.rollout_policy.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/rollouts")
async def rollouts(
    principal: RolloutsReaderDep,
    container: ContainerDep,
    limit: int = Query(default=100, ge=1, le=1000),
):
    return {"items": await container.platform.list_rollouts(limit=limit)}


async def _rollout_action(action, rollout_id: str, principal, container):
    try:
        changed = await action(rollout_id, actor_id=principal.subject)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not changed:
        raise HTTPException(status_code=404, detail="rollout not found")
    rollout = await asyncio.to_thread(container.store.get_configuration_rollout, rollout_id)
    assert rollout is not None
    targets = await asyncio.to_thread(
        container.store.list_configuration_rollout_targets, rollout_id
    )
    return {**rollout.to_dict(), "targets": targets}


@router.post("/rollouts/{rollout_id}/approve")
async def approve_rollout(
    rollout_id: str, principal: RolloutsWriterDep, container: ContainerDep
):
    return await _rollout_action(container.platform.approve_rollout, rollout_id, principal, container)


@router.post("/rollouts/{rollout_id}/cancel")
async def cancel_rollout(
    rollout_id: str, principal: RolloutsWriterDep, container: ContainerDep
):
    return await _rollout_action(container.platform.cancel_rollout, rollout_id, principal, container)


@router.post("/rollouts/{rollout_id}/retry")
async def retry_rollout(
    rollout_id: str, principal: RolloutsWriterDep, container: ContainerDep
):
    return await _rollout_action(container.platform.retry_rollout, rollout_id, principal, container)


@router.post("/rollouts/{rollout_id}/rollback")
async def rollback_rollout(
    rollout_id: str, principal: RolloutsWriterDep, container: ContainerDep
):
    return await _rollout_action(container.platform.rollback_rollout, rollout_id, principal, container)


@router.get("/configuration-events")
async def configuration_events(
    principal: AuditReaderDep,
    container: ContainerDep,
    limit: int = Query(default=200, ge=1, le=2000),
):
    return {"items": await container.platform.list_configuration_events(limit=limit)}


@router.get("/config")
async def config_summary(principal: SettingsReaderDep, container: ContainerDep):
    config = container.config
    providers = {}
    for name, value in config.providers.iter_provider_configs().items():
        api_key = getattr(value, "api_key", "")
        api_base = getattr(value, "api_base", None)
        providers[name] = {
            "configured": bool(api_key or api_base),
            "endpoint": _safe_endpoint(api_base),
        }
    channels = {name: True for name in enabled_channel_ids(config)}
    store = config.runtime.store
    return {
        "auth": {
            "insecure_development_mode": bool(config.gateway.allow_insecure_auth),
            "database_access_tokens": len(
                await asyncio.to_thread(container.store.list_api_access_tokens, limit=5000)
            ),
            "emergency_control_token_configured": bool(os.environ.get("JOYHOUSEBOT_CONTROL_TOKEN")),
        },
        "runtime": {
            "store_backend": "postgres",
            "pool_min_size": store.pool_min_size,
            "pool_max_size": store.pool_max_size,
            "lease_seconds": store.lease_seconds,
            "max_concurrent_sessions": config.gateway.max_concurrent_sessions,
        },
        "providers": providers,
        "channels": channels,
        "extensions": {
            "enabled": list(config.extensions.enabled),
            "discover_entry_points": bool(config.extensions.discover_entry_points),
            "installed": [item.to_dict() for item in installed_extensions()],
        },
        "tools": {
            "optional_allowlist": list(config.tools.optional_allowlist),
        },
    }
