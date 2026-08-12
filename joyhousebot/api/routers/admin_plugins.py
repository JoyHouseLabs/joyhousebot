"""Metadata-only control plane for installed extension releases."""

from __future__ import annotations

import asyncio
from time import time

from fastapi import APIRouter, HTTPException, Query

from joyhousebot.api.dependencies import (
    CapabilitiesPublisherDep,
    ContainerDep,
    ContextDep,
    PlatformAdminDep,
)
from joyhousebot.api.schemas import PluginPlaygroundInvocationRequest, RolloutPolicyRequest
from joyhousebot.application.presenters import record_dict
from joyhousebot.application.runs import GraphTaskCommand
from joyhousebot.bootstrap.extension_catalog import synchronize_extension_inventory
from joyhousebot.domain.capabilities import CapabilityRef, requires_explicit_grant

router = APIRouter(prefix="/admin/plugins", tags=["plugin-control-plane"])


async def _release(container, plugin_id: str) -> dict:
    release = await asyncio.to_thread(container.store.get_plugin_release, plugin_id)
    if release is None:
        raise HTTPException(status_code=404, detail="extension release not found")
    return release


async def _inventory_item(container, extension_id: str) -> dict:
    item = await asyncio.to_thread(
        container.store.get_extension_inventory, extension_id
    )
    if item is None:
        raise HTTPException(status_code=404, detail="extension is not present in catalog")
    release, workers = await asyncio.gather(
        asyncio.to_thread(container.store.get_plugin_release, extension_id),
        asyncio.to_thread(container.store.list_plugin_workers, extension_id),
    )
    live_workers = [
        worker
        for worker in workers
        if worker.get("healthy") and worker.get("plugin") is not None
    ]
    loaded = sum(1 for worker in live_workers if worker.get("execution_eligible"))
    blockers = []
    if not item["source_available"]:
        blockers.append("扩展源码或安装包不可用")
    if not item["installed"]:
        blockers.append("扩展包尚未安装")
    if not item["deployment_allowed"]:
        blockers.append("扩展不在部署 allowlist")
    if bool(item.get("metadata", {}).get("source_conflict")):
        blockers.append("发现多个冲突的扩展源码")
    if release is None:
        blockers.append("尚未运行扩展发现命令")
    elif release["status"] != "active":
        blockers.append("扩展版本尚未发布生效")
    if release is not None and not loaded:
        blockers.append("没有健康 Worker 加载当前生效版本")
    desired = bool(item["desired_active"])
    effective = bool(
        desired
        and item["deployment_allowed"]
        and release is not None
        and release["status"] == "active"
        and loaded
    )
    if effective:
        state = "active"
    elif desired:
        state = "activating"
    elif item["installed"]:
        state = "installed"
    elif item["source_available"]:
        state = "available"
    else:
        state = "unavailable"
    return {
        **item,
        "release": release,
        "worker_summary": {"loaded": loaded, "total": len(live_workers)},
        "effective_active": effective,
        "state": state,
        "activation_blockers": blockers,
    }


async def _inventory(container) -> list[dict]:
    items = await asyncio.to_thread(container.store.list_extension_inventory)
    return [await _inventory_item(container, item["extension_id"]) for item in items]


@router.get("")
async def list_plugins(principal: PlatformAdminDep, container: ContainerDep):
    releases = await asyncio.to_thread(container.store.list_plugin_releases)
    values = []
    for release in releases:
        metrics = await asyncio.to_thread(container.store.get_plugin_metrics, release["plugin_id"])
        components = await asyncio.to_thread(container.store.list_plugin_components, release["plugin_id"])
        values.append({**release, "component_count": len(components), "metrics": metrics})
    return {"items": values}


@router.get("/inventory")
async def extension_inventory(principal: PlatformAdminDep, container: ContainerDep):
    return {
        "console_activation_allowed": bool(
            container.config.extensions.allow_console_activation
        ),
        "items": await _inventory(container),
    }


@router.post("/scan")
async def scan_extensions(principal: PlatformAdminDep, container: ContainerDep):
    await asyncio.to_thread(
        synchronize_extension_inventory, container.config, store=container.store
    )
    return {"items": await _inventory(container)}


@router.get("/{plugin_id}")
async def plugin_overview(plugin_id: str, principal: PlatformAdminDep, container: ContainerDep):
    release = await _release(container, plugin_id)
    components, workers, metrics, releases, inventory = await asyncio.gather(
        asyncio.to_thread(container.store.list_plugin_components, plugin_id),
        asyncio.to_thread(container.store.list_plugin_workers, plugin_id),
        asyncio.to_thread(container.store.get_plugin_metrics, plugin_id),
        asyncio.to_thread(container.store.list_plugin_release_versions, plugin_id),
        asyncio.to_thread(container.store.get_extension_inventory, plugin_id),
    )
    active_loaded = [
        worker for worker in workers
        if worker.get("healthy") and worker.get("plugin") is not None
    ]
    loaded = sum(1 for worker in active_loaded if worker.get("execution_eligible"))
    enriched_components = []
    for component in components:
        value = {**component, "metadata": dict(component.get("metadata") or {})}
        if component.get("component_type") in {"tool", "connector", "skill"}:
            capability_id = str(component.get("reference_id") or "")
            capability_version = str(component.get("reference_version") or "")
            definition = await asyncio.to_thread(
                container.store.get_capability_definition,
                capability_id,
                capability_version,
            )
            exact_definition = await asyncio.to_thread(
                container.store.get_capability_release_definition,
                capability_id,
                capability_version,
            )
            settings = await asyncio.to_thread(
                container.store.get_capability_runtime_settings, capability_id
            )
            runtime_enabled = bool(settings.get("enabled", True))
            blockers = []
            extension_enabled = bool(
                inventory is None
                or (
                    inventory.get("deployment_allowed")
                    and inventory.get("desired_active")
                )
            )
            if not extension_enabled:
                blockers.append("扩展已在控制面停用")
            if not runtime_enabled:
                blockers.append("能力已被操作员停用")
            if definition is None:
                blockers.append("Capability 版本尚未发布")
            if not loaded:
                blockers.append("没有 Worker 加载当前扩展版本")
            value["metadata"].update(
                {
                    "runtime_enabled": runtime_enabled,
                    "worker_loaded": bool(loaded),
                    "execution_ready": (
                        extension_enabled
                        and runtime_enabled
                        and bool(loaded)
                        and definition is not None
                    ),
                    "requires_explicit_grant": (
                        requires_explicit_grant(exact_definition)
                        if exact_definition is not None
                        else False
                    ),
                    "execution_blockers": blockers,
                }
            )
        enriched_components.append(value)
    return {
        "release": release,
        "activation": inventory,
        "releases": releases,
        "components": enriched_components,
        "metrics": metrics,
        "worker_summary": {
            # Only live execution processes that advertised this plugin are
            # meaningful for plugin capacity. Historical rows and schedulers
            # are exposed by /workers but must not dilute this health ratio.
            "total": len(active_loaded),
            "healthy_loaded": loaded,
            "active_runtime_workers": sum(1 for worker in workers if worker.get("healthy")),
            "execution_eligible": loaded,
            "release_mismatch": sum(
                1 for worker in workers
                if worker.get("healthy") and worker.get("plugin") is not None
                and not worker.get("release_matched")
            ),
        },
    }


@router.post("/{plugin_id}/activate", status_code=202)
async def activate_plugin(
    plugin_id: str,
    principal: CapabilitiesPublisherDep,
    container: ContainerDep,
):
    """Request activation only inside the immutable deployment allowlist."""
    if not bool(container.config.extensions.allow_console_activation):
        raise HTTPException(
            status_code=403,
            detail="console extension activation is disabled by deployment policy",
        )
    item = await _inventory_item(container, plugin_id)
    if not item["installed"] or not item["deployment_allowed"]:
        raise HTTPException(
            status_code=409,
            detail="extension must be installed and deployment-allowed before activation",
        )
    if bool(item.get("metadata", {}).get("source_conflict")):
        raise HTTPException(status_code=409, detail="extension catalog sources conflict")
    release = item.get("release")
    if release is None:
        raise HTTPException(
            status_code=409,
            detail="extension release is not discovered; run joyhousebot discover-extensions",
        )
    try:
        if release["status"] not in {"active", "staged"}:
            await container.platform.publish_plugin_release(
                plugin_id,
                str(release["version"]),
                actor_id=principal.subject,
                rollout_policy={
                    "activation_mode": "automatic",
                    "timeout_seconds": 300,
                    "auto_rollback": True,
                    "require_healthy_workers": True,
                },
            )
        else:
            await container.platform.publish_plugin_capabilities(
                plugin_id,
                str(release["version"]),
                actor_id=principal.subject,
                rollout_policy={
                    "activation_mode": "automatic",
                    "timeout_seconds": 300,
                    "auto_rollback": True,
                    "require_healthy_workers": True,
                },
            )
        await asyncio.to_thread(
            container.store.set_extension_desired_active,
            plugin_id,
            True,
            actor_id=principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _inventory_item(container, plugin_id)


@router.post("/{plugin_id}/deactivate")
async def deactivate_plugin(
    plugin_id: str,
    principal: CapabilitiesPublisherDep,
    container: ContainerDep,
):
    """Block new extension executions immediately in the PostgreSQL control plane."""
    if not bool(container.config.extensions.allow_console_activation):
        raise HTTPException(
            status_code=403,
            detail="console extension activation is disabled by deployment policy",
        )
    try:
        await asyncio.to_thread(
            container.store.set_extension_desired_active,
            plugin_id,
            False,
            actor_id=principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _inventory_item(container, plugin_id)


@router.get("/{plugin_id}/components")
async def plugin_components(plugin_id: str, principal: PlatformAdminDep, container: ContainerDep):
    await _release(container, plugin_id)
    return {"items": await asyncio.to_thread(container.store.list_plugin_components, plugin_id)}


@router.get("/{plugin_id}/workers")
async def plugin_workers(plugin_id: str, principal: PlatformAdminDep, container: ContainerDep):
    release = await _release(container, plugin_id)
    workers = await asyncio.to_thread(container.store.list_plugin_workers, plugin_id)
    return {"release": release, "items": workers}


@router.get("/{plugin_id}/metrics")
async def plugin_metrics(
    plugin_id: str,
    principal: PlatformAdminDep,
    container: ContainerDep,
    hours: int = Query(default=24, ge=1, le=2160),
):
    await _release(container, plugin_id)
    return await asyncio.to_thread(container.store.get_plugin_metrics, plugin_id, hours=hours)


@router.get("/{plugin_id}/invocations")
async def plugin_invocations(
    plugin_id: str,
    principal: PlatformAdminDep,
    container: ContainerDep,
    limit: int = Query(default=100, ge=1, le=500),
):
    await _release(container, plugin_id)
    return {"items": await asyncio.to_thread(container.store.list_plugin_recent_invocations, plugin_id, limit=limit)}


@router.get("/{plugin_id}/topology")
async def plugin_topology(plugin_id: str, principal: PlatformAdminDep, container: ContainerDep):
    release = await _release(container, plugin_id)
    components = await asyncio.to_thread(container.store.list_plugin_components, plugin_id)
    component_by_ref = {item["reference_id"]: item for item in components if item["reference_id"]}
    scenarios = await asyncio.to_thread(container.store.list_scenario_versions)
    agents = await asyncio.to_thread(container.store.list_published_agent_profiles)
    nodes = [{"id": f"plugin:{plugin_id}", "kind": "plugin", "label": release["name"]}]
    nodes.extend({"id": f"component:{item['component_id']}", "kind": item["component_type"], "label": item["name"], "data": item} for item in components)
    edges = [{"source": f"plugin:{plugin_id}", "target": f"component:{item['component_id']}", "kind": "owns"} for item in components]
    for scenario in scenarios:
        scenario_node = f"scenario:{scenario.scenario_id}:{scenario.version}"
        relevant = [
            value for value in scenario.allowed_capabilities
            if value.capability_id in component_by_ref
        ]
        if not relevant:
            continue
        nodes.append({"id": scenario_node, "kind": "scenario", "label": scenario.name, "data": {"scenario_id": scenario.scenario_id, "version": scenario.version}})
        for capability in relevant:
            edges.append({"source": f"component:{component_by_ref[capability.capability_id]['component_id']}", "target": scenario_node, "kind": "allowed_by", "data": {"capability": capability.to_dict()}})
    for profile in agents:
        bindings = await asyncio.to_thread(container.store.list_agent_skill_bindings, profile.revision.revision_id)
        relevant = [item for item in bindings if item["skill_id"] in component_by_ref]
        if not relevant:
            continue
        agent_node = f"agent:{profile.definition.agent_id}:{profile.revision.revision_id}"
        nodes.append({"id": agent_node, "kind": "agent", "label": profile.definition.name, "data": {"agent_id": profile.definition.agent_id, "revision_id": profile.revision.revision_id}})
        for binding in relevant:
            edges.append({"source": f"component:{component_by_ref[binding['skill_id']]['component_id']}", "target": agent_node, "kind": binding["activation_mode"]})
    return {"release": release, "nodes": nodes, "edges": edges}


@router.get("/{plugin_id}/health")
async def plugin_health(plugin_id: str, principal: PlatformAdminDep, container: ContainerDep):
    release = await _release(container, plugin_id)
    workers, components = await asyncio.gather(
        asyncio.to_thread(container.store.list_plugin_workers, plugin_id),
        asyncio.to_thread(container.store.list_plugin_components, plugin_id),
    )
    loaded = [
        item for item in workers if item.get("healthy") and item.get("plugin") is not None
    ]
    healthy = [item for item in loaded if item.get("execution_eligible")]
    baseline_checks = [
        {
            "name": "release_catalog",
            "status": "healthy",
            "summary": f"immutable release metadata; {len(components)} registered components",
        },
        {"name": "worker_release", "status": "healthy" if healthy else "degraded", "summary": f"{len(healthy)}/{len(loaded)} live plugin workers match this exact release"},
    ]
    provider_checks: dict[str, list[dict[str, str]]] = {}
    for worker in healthy:
        plugin = dict(worker.get("plugin") or {})
        for check in plugin.get("health_checks") or ():
            if not isinstance(check, dict) or not str(check.get("name") or "").strip():
                continue
            provider_checks.setdefault(str(check["name"]), []).append(check)
    severity = {"healthy": 0, "degraded": 1, "failed": 2}
    for name, values in sorted(provider_checks.items()):
        status = max(
            (str(item.get("status") or "degraded") for item in values),
            key=lambda item: severity.get(item, 1),
        )
        summaries = list(
            dict.fromkeys(str(item.get("summary") or "") for item in values)
        )
        baseline_checks.append(
            {
                "name": name,
                "status": status,
                "summary": "; ".join(item for item in summaries if item),
            }
        )
    return {
        "release": release,
        "status": (
            "healthy"
            if all(item["status"] == "healthy" for item in baseline_checks)
            else "degraded"
        ),
        "checks": baseline_checks,
    }


@router.post("/{plugin_id}/versions/{version}/publish", status_code=202)
async def publish_plugin_release(
    plugin_id: str,
    version: str,
    principal: CapabilitiesPublisherDep,
    container: ContainerDep,
    body: RolloutPolicyRequest | None = None,
):
    """Stage an installed extension build for its declared Worker role."""
    try:
        return await container.platform.publish_plugin_release(
            plugin_id,
            version,
            actor_id=principal.subject,
            rollout_policy=body.model_dump() if body is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{plugin_id}/playground/runs", status_code=202)
async def create_plugin_playground_run(
    plugin_id: str,
    body: PluginPlaygroundInvocationRequest,
    context: ContextDep,
    principal: PlatformAdminDep,
    container: ContainerDep,
):
    """Create one durable direct Tool Run without coordinator/model planning.

    This remains inside the normal distributed task runtime so that input,
    invocation, duration and produced Artifacts can be inspected or replayed.
    It deliberately permits only a plugin-owned, enabled, side-effect-free
    executable capability; the Playground cannot become an administrative
    bypass for write operations.
    """
    del principal
    release = await _release(container, plugin_id)
    definition = await asyncio.to_thread(
        container.store.get_capability_definition, body.capability_id
    )
    if definition is None:
        raise HTTPException(status_code=404, detail="capability not found")
    ref = CapabilityRef.from_dict(dict(definition.get("ref") or {}))
    if ref.plugin_id != release["plugin_id"]:
        raise HTTPException(status_code=422, detail="capability is not owned by this extension")
    if ref.kind.value not in {"tool", "connector"}:
        raise HTTPException(status_code=422, detail="Playground only executes Tools or Connectors")
    if str(definition.get("side_effect") or "none") != "none":
        raise HTTPException(status_code=422, detail="Playground refuses capabilities with side effects")
    settings = await asyncio.to_thread(
        container.store.get_capability_runtime_settings, ref.capability_id
    )
    if not bool(settings.get("enabled", True)):
        raise HTTPException(status_code=409, detail="capability is disabled")

    manifest = dict(release.get("manifest") or {})
    agent_id = str(manifest.get("default_agent_id") or "default")
    session_id = body.session_id or f"playground_{int(time() * 1000):x}"
    try:
        record = await container.runs.create_graph(
            context,
            goal=f"Tool Playground: {ref.capability_id}",
            agent_id=agent_id,
            session_id=session_id,
            max_concurrent=1,
            fail_fast=True,
            aggregate=False,
            tasks=[
                GraphTaskCommand(
                    id="direct-tool",
                    name=f"Playground · {ref.capability_id}",
                    prompt="Execute the pinned capability with the supplied Playground input.",
                    timeout_seconds=float(definition.get("timeout_seconds") or 60),
                    max_attempts=1,
                    metadata={
                        "source": "plugin_playground",
                        "plugin_id": plugin_id,
                        "direct_tool": True,
                    },
                    capability=ref,
                    capability_input=body.input,
                    allowed_tools=[ref.capability_id],
                )
            ],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return record_dict(record)
