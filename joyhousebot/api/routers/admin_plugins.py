"""Business-neutral observability API for installed capability plugins."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from joyhousebot.api.dependencies import ContainerDep, PlatformAdminDep
from joyhousebot.application.plugins import run_plugin_diagnostics

router = APIRouter(prefix="/admin/plugins", tags=["plugin-control-plane"])


async def _release(container, plugin_id: str) -> dict:
    release = await asyncio.to_thread(container.store.get_plugin_release, plugin_id)
    if release is None:
        raise HTTPException(status_code=404, detail="plugin release not found")
    return release


@router.get("")
async def list_plugins(principal: PlatformAdminDep, container: ContainerDep):
    releases = await asyncio.to_thread(container.store.list_plugin_releases)
    values = []
    for release in releases:
        metrics = await asyncio.to_thread(container.store.get_plugin_metrics, release["plugin_id"])
        components = await asyncio.to_thread(container.store.list_plugin_components, release["plugin_id"])
        values.append({**release, "component_count": len(components), "metrics": metrics})
    return {"items": values}


@router.get("/{plugin_id}")
async def plugin_overview(plugin_id: str, principal: PlatformAdminDep, container: ContainerDep):
    release = await _release(container, plugin_id)
    components, workers, metrics = await asyncio.gather(
        asyncio.to_thread(container.store.list_plugin_components, plugin_id),
        asyncio.to_thread(container.store.list_plugin_workers, plugin_id),
        asyncio.to_thread(container.store.get_plugin_metrics, plugin_id),
    )
    loaded = sum(1 for worker in workers if worker.get("plugin") is not None and worker.get("healthy"))
    return {
        "release": release,
        "components": components,
        "metrics": metrics,
        "worker_summary": {"total": len(workers), "healthy_loaded": loaded},
    }


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
        relevant = [value for value in scenario.allowed_capabilities if value in component_by_ref]
        if not relevant:
            continue
        nodes.append({"id": scenario_node, "kind": "scenario", "label": scenario.name, "data": {"scenario_id": scenario.scenario_id, "version": scenario.version}})
        for capability_id in relevant:
            edges.append({"source": f"component:{component_by_ref[capability_id]['component_id']}", "target": scenario_node, "kind": "allowed_by"})
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
    loaded = [item for item in workers if item.get("plugin")]
    healthy = [item for item in loaded if item.get("healthy")]
    baseline_checks = [
        {"name": "catalog", "status": "healthy" if components else "failed", "summary": f"{len(components)} registered components"},
        {"name": "worker_release", "status": "healthy" if healthy else "degraded", "summary": f"{len(healthy)}/{len(workers)} healthy workers loaded this plugin"},
    ]
    persisted = await asyncio.to_thread(container.store.list_plugin_check_results, plugin_id)
    checks = persisted or baseline_checks
    return {
        "release": release,
        "status": "healthy" if checks and all(item["status"] == "healthy" for item in checks) else "degraded",
        "checks": checks,
        "last_diagnostic_at": max((item.get("created_at") or "" for item in persisted), default=None),
    }


@router.post("/{plugin_id}/diagnostics")
async def plugin_diagnostics(plugin_id: str, principal: PlatformAdminDep, container: ContainerDep):
    await _release(container, plugin_id)
    try:
        checks = await run_plugin_diagnostics(
            config=container.config, store=container.store, plugin_id=plugin_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"items": checks}
