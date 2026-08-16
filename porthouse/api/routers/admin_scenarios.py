"""Scenario Studio HTTP endpoints for business configuration."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from porthouse.api.dependencies import (
    ContainerDep,
    ScenarioPublisherDep,
    ScenarioReaderDep,
    ScenarioWriterDep,
)
from porthouse.api.schemas import (
    RolloutPolicyRequest,
    SaveScenarioVersionRequest,
    SimulateScenarioRequest,
)
from porthouse.application.errors import ConflictError, NotFoundError
from porthouse.application.presenters import public_capability_definition
from porthouse.domain.capabilities.models import CapabilityRef
from porthouse.domain.scenarios import (
    ClarificationEdge,
    ClarificationNode,
    ScenarioField,
    ScenarioVersion,
)
from porthouse.domain.skills import SkillRef

router = APIRouter(prefix="/admin/scenarios", tags=["scenario-studio"])


@router.get("/capability-catalog")
async def capability_catalog(principal: ScenarioReaderDep, container: ContainerDep):
    rows = await asyncio.to_thread(container.store.list_capability_definitions)
    return {"items": [public_capability_definition(row) for row in rows]}


@router.get("/skill-catalog")
async def skill_catalog(principal: ScenarioReaderDep, container: ContainerDep):
    rows = await asyncio.to_thread(container.store.list_skills, active_only=True)
    return {
        "items": [
            {
                "skill_id": row["skill_id"],
                "name": row["name"],
                "description": row["description"],
                "version": row["current"]["version"],
                "content_sha256": row["current"]["content_sha256"],
            }
            for row in rows
            if row.get("current")
        ]
    }


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
        required_skills=tuple(
            SkillRef.from_dict(item.model_dump())
            for item in body.required_skills
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
    body: RolloutPolicyRequest | None = None,
):
    try:
        return await container.scenarios.publish(
            scenario_id,
            version,
            actor_id=principal.subject,
            rollout_policy=body.model_dump() if body is not None else None,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{scenario_id}/simulate")
async def simulate(
    scenario_id: str,
    body: SimulateScenarioRequest,
    principal: ScenarioReaderDep,
    container: ContainerDep,
):
    try:
        return await container.scenarios.simulate(
            scenario_id,
            prompt=body.prompt,
            inputs=body.inputs,
            version=body.version,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
