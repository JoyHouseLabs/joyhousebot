"""Control API for versioned model provider configurations and model catalogs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from porthouse.api.dependencies import ContainerDep, SettingsReaderDep, SettingsWriterDep
from porthouse.api.schemas import ModelProviderRevisionRequest, RolloutPolicyRequest

router = APIRouter(prefix="/admin/model-providers", tags=["model-provider-control-plane"])


def _configuration(body: ModelProviderRevisionRequest) -> dict:
    return {
        "enabled": body.enabled,
        "extension_id": body.extension_id,
        "api_base": body.api_base,
        "api_key_ref": body.api_key_ref,
        "allow_insecure_http": body.allow_insecure_http,
        "credential_mode": body.credential_mode,
        "extra_header_refs": body.extra_header_refs,
        "request_timeout_seconds": body.request_timeout_seconds,
        "models": body.models,
    }


@router.get("")
async def list_model_providers(principal: SettingsReaderDep, container: ContainerDep):
    return {"items": await container.model_providers.list_providers()}


@router.get("/models")
async def list_models(principal: SettingsReaderDep, container: ContainerDep):
    return {"items": await container.model_providers.list_models()}


@router.post("", status_code=201)
async def create_model_provider(
    body: ModelProviderRevisionRequest,
    principal: SettingsWriterDep,
    container: ContainerDep,
):
    if not body.provider_id:
        raise HTTPException(status_code=422, detail="provider_id is required")
    try:
        return await container.model_providers.save_revision(
            body.provider_id,
            name=body.name,
            description=body.description,
            configuration=_configuration(body),
            actor_id=principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{provider_id}")
async def get_model_provider(
    provider_id: str,
    principal: SettingsReaderDep,
    container: ContainerDep,
):
    value = await container.model_providers.get_provider(provider_id)
    if value is None:
        raise HTTPException(status_code=404, detail="model provider not found")
    return value


@router.post("/{provider_id}/revisions", status_code=201)
async def create_model_provider_revision(
    provider_id: str,
    body: ModelProviderRevisionRequest,
    principal: SettingsWriterDep,
    container: ContainerDep,
):
    try:
        return await container.model_providers.save_revision(
            provider_id,
            name=body.name,
            description=body.description,
            configuration=_configuration(body),
            actor_id=principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{provider_id}/revisions/{revision_id}/publish", status_code=202)
async def publish_model_provider_revision(
    provider_id: str,
    revision_id: str,
    body: RolloutPolicyRequest,
    principal: SettingsWriterDep,
    container: ContainerDep,
):
    try:
        return await container.model_providers.publish_revision(
            provider_id,
            revision_id,
            actor_id=principal.subject,
            rollout_policy=body.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


__all__ = ["router"]
