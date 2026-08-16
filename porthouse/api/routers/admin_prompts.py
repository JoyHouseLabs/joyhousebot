"""Prompt asset authoring and immutable Agent-revision binding endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from porthouse.api.dependencies import (
    ContainerDep,
    PromptsPublisherDep,
    PromptsReaderDep,
    PromptsWriterDep,
)
from porthouse.api.prompt_schemas import BindPromptRevisionRequest, SavePromptDraftRequest

router = APIRouter(prefix="/admin/prompts", tags=["prompts"])


@router.get("")
async def list_prompts(principal: PromptsReaderDep, container: ContainerDep):
    return {"items": await container.prompts.list()}


@router.get("/{prompt_id}")
async def get_prompt(prompt_id: str, principal: PromptsReaderDep, container: ContainerDep):
    try:
        return await container.prompts.get(prompt_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{prompt_id}/versions/{version}")
async def save_prompt_draft(
    prompt_id: str,
    version: int,
    body: SavePromptDraftRequest,
    principal: PromptsWriterDep,
    container: ContainerDep,
):
    if body.prompt_id != prompt_id or body.version != version:
        raise HTTPException(status_code=400, detail="body prompt_id/version must match path")
    try:
        return await container.prompts.save_draft(body.model_dump(), actor_id=principal.subject)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{prompt_id}/versions/{version}/validate")
async def validate_prompt(
    prompt_id: str,
    version: int,
    principal: PromptsWriterDep,
    container: ContainerDep,
):
    try:
        return await container.prompts.validate(prompt_id, version, actor_id=principal.subject)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{prompt_id}/versions/{version}/publish")
async def publish_prompt(
    prompt_id: str,
    version: int,
    principal: PromptsPublisherDep,
    container: ContainerDep,
):
    try:
        return await container.prompts.publish(prompt_id, version, actor_id=principal.subject)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/bindings")
async def bind_prompt(
    body: BindPromptRevisionRequest,
    principal: PromptsPublisherDep,
    container: ContainerDep,
):
    try:
        return await container.prompts.bind(body.model_dump(), actor_id=principal.subject)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
