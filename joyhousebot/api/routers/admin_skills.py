"""Independent Skill authoring and publication control plane."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from joyhousebot.api.dependencies import (
    ContainerDep,
    SkillsPublisherDep,
    SkillsReaderDep,
    SkillsWriterDep,
)
from joyhousebot.api.schemas import RolloutPolicyRequest
from joyhousebot.api.skill_schemas import SaveSkillDraftRequest, SetSkillStatusRequest

router = APIRouter(prefix="/admin/skills", tags=["skills"])


@router.get("")
async def list_skills(principal: SkillsReaderDep, container: ContainerDep):
    return {"items": await container.skills.list()}


@router.post("")
async def create_skill_draft(
    body: SaveSkillDraftRequest,
    principal: SkillsWriterDep,
    container: ContainerDep,
):
    try:
        return await container.skills.save_draft(
            body.model_dump(), actor_id=principal.subject
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{skill_id}")
async def get_skill(
    skill_id: str, principal: SkillsReaderDep, container: ContainerDep
):
    try:
        return await container.skills.get(skill_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{skill_id}/versions/{version}")
async def save_skill_draft(
    skill_id: str,
    version: str,
    body: SaveSkillDraftRequest,
    principal: SkillsWriterDep,
    container: ContainerDep,
):
    if body.skill_id != skill_id or body.version != version:
        raise HTTPException(
            status_code=400, detail="body skill_id/version must match path"
        )
    try:
        return await container.skills.save_draft(
            body.model_dump(), actor_id=principal.subject
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{skill_id}/versions/{version}/validate")
async def validate_skill_version(
    skill_id: str,
    version: str,
    principal: SkillsWriterDep,
    container: ContainerDep,
):
    try:
        return await container.skills.validate(
            skill_id, version, actor_id=principal.subject
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{skill_id}/versions/{version}/publish")
async def publish_skill_version(
    skill_id: str,
    version: str,
    principal: SkillsPublisherDep,
    container: ContainerDep,
    body: RolloutPolicyRequest | None = None,
):
    try:
        return await container.skills.publish(
            skill_id,
            version,
            actor_id=principal.subject,
            rollout_policy=body.model_dump() if body is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/{skill_id}/status")
async def set_skill_status(
    skill_id: str,
    body: SetSkillStatusRequest,
    principal: SkillsPublisherDep,
    container: ContainerDep,
):
    try:
        return await container.skills.set_status(
            skill_id, status=body.status, actor_id=principal.subject
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
