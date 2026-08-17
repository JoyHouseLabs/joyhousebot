"""AgentTeam revision, publication, and Workspace inspection endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from porthouse.api.agent_team_schemas import (
    SaveAgentTeamRevisionRequest,
    ValidateBlueprintRequest,
)
from porthouse.api.dependencies import (
    ContainerDep,
    TeamsPublisherDep,
    TeamsReaderDep,
    TeamsWriterDep,
)
from porthouse.domain.agent_teams import AgentTeamMember, AgentTeamRevision

router = APIRouter(prefix="/admin/teams", tags=["agent-teams"])


@router.get("")
async def list_teams(principal: TeamsReaderDep, container: ContainerDep):
    return {"items": await container.agent_teams.list_catalog()}


@router.get("/blueprint-presets")
async def list_blueprint_presets(principal: TeamsReaderDep, container: ContainerDep):
    return {"items": await container.agent_teams.presets()}


@router.post("/blueprint-validate")
async def validate_blueprint(
    body: ValidateBlueprintRequest, principal: TeamsReaderDep, container: ContainerDep
):
    return await container.agent_teams.validate_blueprint(
        {
            "blueprint": body.blueprint,
            "members": [item.model_dump() for item in body.members],
            "coordinator_member_id": body.coordinator_member_id,
            "budget_policy": body.budget_policy,
        }
    )


@router.get("/{team_id}/rollout/latest")
async def latest_team_rollout(
    team_id: str, principal: TeamsReaderDep, container: ContainerDep
):
    rollout = await asyncio.to_thread(
        container.store.get_latest_configuration_rollout, "agent_team", team_id
    )
    if rollout is None:
        raise HTTPException(status_code=404, detail="team has no rollout history")
    targets = await asyncio.to_thread(
        container.store.list_configuration_rollout_targets, rollout.rollout_id
    )
    return {**rollout.to_dict(), "targets": targets}


@router.post("/{team_id}/blueprint-migrate")
async def migrate_team_blueprint(
    team_id: str, principal: TeamsWriterDep, container: ContainerDep
):
    return await container.agent_teams.migrate_blueprint(
        team_id, actor_id=principal.subject
    )


@router.get("/{team_id}/revisions")
async def list_team_revisions(
    team_id: str, principal: TeamsReaderDep, container: ContainerDep
):
    return {"items": await container.agent_teams.list_revisions(team_id)}


@router.put("/{team_id}/revisions/{revision_id}")
async def save_team_revision(
    team_id: str,
    revision_id: str,
    body: SaveAgentTeamRevisionRequest,
    principal: TeamsWriterDep,
    container: ContainerDep,
):
    if body.revision_id != revision_id:
        raise HTTPException(status_code=400, detail="body revision_id must match path")
    blueprint = dict(body.collaboration_blueprint or {})
    if body.role_bindings:
        blueprint.setdefault("role_bindings", body.role_bindings)
    revision = AgentTeamRevision(
        team_id=team_id,
        revision_id=revision_id,
        version=body.version,
        name=body.name,
        description=body.description,
        coordinator_member_id=body.coordinator_member_id,
        members=tuple(
            AgentTeamMember.from_dict(item.model_dump()) for item in body.members
        ),
        context_policy=body.context_policy,
        budget_policy=body.budget_policy,
        approval_policy=body.approval_policy,
        collaboration_blueprint=blueprint or None,
        status="draft",
        created_by=principal.subject,
    )
    return await container.agent_teams.save_draft(revision)


@router.post("/{team_id}/revisions/{revision_id}/publish")
async def publish_team_revision(
    team_id: str,
    revision_id: str,
    principal: TeamsPublisherDep,
    container: ContainerDep,
):
    return await container.agent_teams.publish(
        team_id, revision_id, actor_id=principal.subject
    )


@router.get("/{team_id}/events")
async def list_team_events(
    team_id: str,
    principal: TeamsReaderDep,
    container: ContainerDep,
    limit: int = Query(default=200, ge=1, le=1000),
):
    rows = await asyncio.to_thread(
        container.store.list_agent_team_events, team_id, limit=limit
    )
    return {"items": rows}


@router.get("/{team_id}/runs/{run_id}/workspace")
async def inspect_team_workspace(
    team_id: str,
    run_id: str,
    principal: TeamsReaderDep,
    container: ContainerDep,
    limit: int = Query(default=100, ge=1, le=200),
):
    run = await asyncio.to_thread(container.store.get_runtime_run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    metadata = dict(dict(run.options or {}).get("metadata") or {})
    team_ref = dict(metadata.get("team_ref") or {})
    if str(team_ref.get("team_id") or "") != team_id:
        raise HTTPException(status_code=404, detail="Run does not use this AgentTeam")
    root_run_id = run.root_run_id or run.run_id
    rows = await asyncio.to_thread(
        container.store.list_team_workspace_entries,
        user_id=run.user_id,
        root_run_id=root_run_id,
        reader_member_id=str(team_ref.get("coordinator_member_id") or ""),
        coordinator=True,
        limit=limit,
    )
    return {"items": rows, "team_ref": team_ref, "root_run_id": root_run_id}
