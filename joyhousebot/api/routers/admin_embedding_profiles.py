"""Control API for versioned Knowledge embedding profiles."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from joyhousebot.api.dependencies import ContainerDep, SettingsReaderDep, SettingsWriterDep
from joyhousebot.api.schemas import EmbeddingProfileRevisionRequest

router = APIRouter(prefix="/admin/embedding-profiles", tags=["embedding-profile-control-plane"])


def _configuration(body: EmbeddingProfileRevisionRequest) -> dict:
    return {
        "provider_id": body.provider_id,
        "provider_revision_id": body.provider_revision_id,
        "model_id": body.model_id,
        "dimensions": body.dimensions,
        "normalization": body.normalization,
        "batch_size": body.batch_size,
        "max_input_tokens": body.max_input_tokens,
        "max_cost_usd": body.max_cost_usd,
        "requests_per_minute": body.requests_per_minute,
        "tokens_per_minute": body.tokens_per_minute,
        "ann_min_rows": body.ann_min_rows,
        "hnsw_m": body.hnsw_m,
        "hnsw_ef_construction": body.hnsw_ef_construction,
        "hnsw_ef_search": body.hnsw_ef_search,
        "is_default": body.is_default,
    }


@router.get("")
async def list_embedding_profiles(
    principal: SettingsReaderDep, container: ContainerDep
):
    return {"items": await container.embedding_profiles.list_profiles()}


@router.get("/readiness")
async def get_vector_readiness(principal: SettingsReaderDep, container: ContainerDep):
    return await container.embedding_profiles.readiness()


@router.post("", status_code=201)
async def create_embedding_profile(
    body: EmbeddingProfileRevisionRequest,
    principal: SettingsWriterDep,
    container: ContainerDep,
):
    if not body.profile_id:
        raise HTTPException(status_code=422, detail="profile_id is required")
    try:
        return await container.embedding_profiles.save_revision(
            body.profile_id,
            name=body.name,
            description=body.description,
            configuration=_configuration(body),
            actor_id=principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{profile_id}")
async def get_embedding_profile(
    profile_id: str, principal: SettingsReaderDep, container: ContainerDep
):
    value = await container.embedding_profiles.get_profile(profile_id)
    if value is None:
        raise HTTPException(status_code=404, detail="embedding profile not found")
    return value


@router.post("/{profile_id}/revisions", status_code=201)
async def create_embedding_profile_revision(
    profile_id: str,
    body: EmbeddingProfileRevisionRequest,
    principal: SettingsWriterDep,
    container: ContainerDep,
):
    try:
        return await container.embedding_profiles.save_revision(
            profile_id,
            name=body.name,
            description=body.description,
            configuration=_configuration(body),
            actor_id=principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{profile_id}/revisions/{revision_id}/publish")
async def publish_embedding_profile_revision(
    profile_id: str,
    revision_id: str,
    principal: SettingsWriterDep,
    container: ContainerDep,
):
    try:
        return await container.embedding_profiles.publish_revision(
            profile_id, revision_id, actor_id=principal.subject
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


__all__ = ["router"]
