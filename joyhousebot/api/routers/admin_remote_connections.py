"""Control API for versioned remote Capability service connections."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from joyhousebot.api.dependencies import (
    CapabilitiesPublisherDep,
    ContainerDep,
    SettingsReaderDep,
    SettingsWriterDep,
)
from joyhousebot.api.schemas import (
    RemoteConnectionRevisionRequest,
    RolloutPolicyRequest,
)

router = APIRouter(
    prefix="/admin/remote-connections",
    tags=["remote-capability-connections"],
)


def _configuration(body: RemoteConnectionRevisionRequest) -> dict:
    return {
        "enabled": body.enabled,
        "base_url": body.base_url,
        "key_id": body.key_id,
        "signing_secret_ref": body.signing_secret_ref,
        "allow_insecure_http": body.allow_insecure_http,
        "require_response_signature": body.require_response_signature,
        "timeout_seconds": body.timeout_seconds,
        "max_response_bytes": body.max_response_bytes,
        "capabilities": body.capabilities,
    }


@router.get("")
async def list_remote_connections(
    principal: SettingsReaderDep, container: ContainerDep
):
    return {"items": await container.remote_connections.list_connections()}


@router.post("", status_code=201)
async def create_remote_connection(
    body: RemoteConnectionRevisionRequest,
    principal: SettingsWriterDep,
    container: ContainerDep,
):
    if not body.connection_id:
        raise HTTPException(status_code=422, detail="connection_id is required")
    return await container.remote_connections.save_revision(
        body.connection_id,
        name=body.name,
        description=body.description,
        configuration=_configuration(body),
        actor_id=principal.subject,
    )


@router.get("/{connection_id}")
async def get_remote_connection(
    connection_id: str,
    principal: SettingsReaderDep,
    container: ContainerDep,
):
    value = await container.remote_connections.get_connection(connection_id)
    if value is None:
        raise HTTPException(status_code=404, detail="remote connection not found")
    return value


@router.post("/{connection_id}/revisions", status_code=201)
async def create_remote_connection_revision(
    connection_id: str,
    body: RemoteConnectionRevisionRequest,
    principal: SettingsWriterDep,
    container: ContainerDep,
):
    return await container.remote_connections.save_revision(
        connection_id,
        name=body.name,
        description=body.description,
        configuration=_configuration(body),
        actor_id=principal.subject,
    )


@router.post("/{connection_id}/revisions/{revision_id}/publish", status_code=202)
async def publish_remote_connection_revision(
    connection_id: str,
    revision_id: str,
    body: RolloutPolicyRequest,
    principal: SettingsWriterDep,
    container: ContainerDep,
):
    try:
        return await container.remote_connections.publish_revision(
            connection_id,
            revision_id,
            actor_id=principal.subject,
            rollout_policy=body.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{connection_id}/capabilities/{capability_id}/versions/{version}/publish",
    status_code=202,
)
async def publish_remote_capability(
    connection_id: str,
    capability_id: str,
    version: str,
    body: RolloutPolicyRequest,
    principal: CapabilitiesPublisherDep,
    container: ContainerDep,
):
    try:
        return await container.remote_connections.publish_capability(
            connection_id,
            capability_id,
            version,
            actor_id=principal.subject,
            rollout_policy=body.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


__all__ = ["router"]
