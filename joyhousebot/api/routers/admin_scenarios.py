"""Scenario Studio HTTP endpoints for business configuration."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from joyhousebot.api.dependencies import (
    ContainerDep,
    ScenarioPublisherDep,
    ScenarioReaderDep,
    ScenarioWriterDep,
)
from joyhousebot.api.schemas import SaveScenarioVersionRequest, SimulateScenarioRequest
from joyhousebot.application.presenters import public_capability_definition
from joyhousebot.domain.capabilities.models import CapabilityRef
from joyhousebot.domain.scenarios import (
    ClarificationEdge,
    ClarificationNode,
    ScenarioField,
    ScenarioVersion,
)
from joyhousebot.orchestration import ClarificationEngine, ScenarioRouter

router = APIRouter(prefix="/admin/scenarios", tags=["scenario-studio"])


@router.get("/capability-catalog")
async def capability_catalog(principal: ScenarioReaderDep, container: ContainerDep):
    rows = await asyncio.to_thread(container.store.list_capability_definitions)
    return {"items": [public_capability_definition(row) for row in rows]}


@router.get("")
async def list_versions(principal: ScenarioReaderDep, container: ContainerDep):
    rows = await asyncio.to_thread(
        container.store.list_scenario_versions, published_only=False
    )
    return {"items": [row.to_dict() for row in rows]}


@router.put("/{scenario_id}/versions/{version}")
async def save_version(
    scenario_id: str,
    version: int,
    body: SaveScenarioVersionRequest,
    principal: ScenarioWriterDep,
    container: ContainerDep,
):
    if body.version != version:
        raise HTTPException(status_code=400, detail="body version must match path version")
    scenario = ScenarioVersion(
        scenario_id=scenario_id,
        version=version,
        name=body.name,
        description=body.description,
        fields=tuple(ScenarioField(**item.model_dump()) for item in body.fields),
        nodes=tuple(ClarificationNode(**item.model_dump()) for item in body.nodes),
        edges=tuple(ClarificationEdge(**item.model_dump()) for item in body.edges),
        allowed_capabilities=tuple(
            CapabilityRef.from_dict(item.model_dump()) for item in body.allowed_capabilities
        ),
        planning_mode=body.planning_mode,
        execution_policy=body.execution_policy,
        routing_rules=tuple(body.routing_rules),
    )
    try:
        await asyncio.to_thread(
            container.store.save_scenario_version,
            scenario,
            actor_id=principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return scenario.to_dict()


@router.post("/{scenario_id}/versions/{version}/publish")
async def publish_version(
    scenario_id: str,
    version: int,
    principal: ScenarioPublisherDep,
    container: ContainerDep,
):
    versions = await asyncio.to_thread(
        container.store.list_scenario_versions, published_only=False
    )
    draft = next(
        (
            item
            for item in versions
            if item.scenario_id == scenario_id and item.version == version
        ),
        None,
    )
    if draft is None:
        raise HTTPException(status_code=404, detail="scenario version not found")
    checks = await asyncio.gather(
        *(
            asyncio.to_thread(
                container.store.get_capability_definition,
                item.capability_id,
                item.version,
            )
            for item in draft.allowed_capabilities
        )
    )
    unknown = [
        item.to_dict()
        for item, definition in zip(draft.allowed_capabilities, checks, strict=True)
        if definition is None
        or CapabilityRef.from_dict(dict(definition["ref"])).identity != item.identity
    ]
    if unknown:
        raise HTTPException(
            status_code=409,
            detail=f"scenario references unpublished capabilities: {sorted(unknown)}",
        )
    try:
        await asyncio.to_thread(
            container.store.publish_scenario,
            scenario_id,
            version,
            actor_id=principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    scenario = await asyncio.to_thread(container.store.get_scenario_version, scenario_id, version)
    return scenario.to_dict()


@router.post("/{scenario_id}/simulate")
async def simulate(
    scenario_id: str,
    body: SimulateScenarioRequest,
    principal: ScenarioReaderDep,
    container: ContainerDep,
):
    versions = await asyncio.to_thread(
        container.store.list_scenario_versions, published_only=False
    )
    candidates = [
        item
        for item in versions
        if item.scenario_id == scenario_id
        and (body.version is None or item.version == body.version)
    ]
    if not candidates:
        raise HTTPException(status_code=404, detail="scenario version not found")
    scenario = max(candidates, key=lambda item: item.version)
    router = ScenarioRouter(container.store)
    decision = router.decision_for(
        scenario, body.inputs, 1.0, "STUDIO_SIMULATION"
    )
    live_decision, live_scenario = router.route(body.prompt, supplied_inputs=body.inputs)
    step = ClarificationEngine(container.store).evaluate(scenario, decision.extracted_inputs)
    return {
        "target_scenario": {
            "scenario_id": scenario.scenario_id,
            "version": scenario.version,
            "matched": any(item["matched"] for item in router.explain_match(scenario, body.prompt)),
            "rule_evaluations": router.explain_match(scenario, body.prompt),
        },
        "live_route": {
            "scenario_id": live_scenario.scenario_id if live_scenario else None,
            "version": live_scenario.version if live_scenario else None,
            "name": live_scenario.name if live_scenario else None,
            "reason_code": live_decision.reason_code,
            "next_action": live_decision.next_action,
        },
        "routing_decision": decision.to_dict(),
        "next_question": (
            {
                "node_id": step.node.node_id,
                "question": step.node.question,
                "fields": list(step.node.field_names),
            }
            if step.node
            else None
        ),
        "ready": step.complete,
    }
