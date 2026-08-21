"""App Package catalog, validation, and installation lifecycle endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from joyhousebot.api.app_schemas import (
    AppInstallationActionRequest,
    CreateAppClientRequest,
    CreateOwnerClientRequest,
    InstallAppReleaseRequest,
    RotateOwnerClientKeyRequest,
    SaveAppReleaseRequest,
    UpdateOwnerClientRequest,
)
from joyhousebot.api.dependencies import (
    AppsInstallerDep,
    AppsPublisherDep,
    AppsReaderDep,
    AppsWriterDep,
    ContainerDep,
)

router = APIRouter(prefix="/admin/apps", tags=["app-releases"])


def _user_id(principal: object) -> str:
    value = str(getattr(principal, "user_id", None) or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="App Package operation requires a user_id")
    return value


@router.get("/owner-clients")
async def list_owner_clients(
    principal: AppsReaderDep,
    container: ContainerDep,
):
    return {"items": await container.owner_delegation.list_clients()}


@router.post("/owner-clients", status_code=201)
async def create_owner_client(
    body: CreateOwnerClientRequest,
    principal: AppsWriterDep,
    container: ContainerDep,
):
    return await container.owner_delegation.create_client(
        **body.model_dump(), actor_id=principal.subject
    )


@router.post("/owner-clients/{client_id}/rotate-key")
async def rotate_owner_client_key(
    client_id: str,
    body: RotateOwnerClientKeyRequest,
    principal: AppsWriterDep,
    container: ContainerDep,
):
    return await container.owner_delegation.rotate_client_key(
        client_id, **body.model_dump(), actor_id=principal.subject
    )


@router.put("/owner-clients/{client_id}")
async def update_owner_client(
    client_id: str,
    body: UpdateOwnerClientRequest,
    principal: AppsWriterDep,
    container: ContainerDep,
):
    return await container.owner_delegation.update_client(
        client_id, **body.model_dump(), actor_id=principal.subject
    )


@router.delete("/owner-clients/{client_id}")
async def revoke_owner_client(
    client_id: str,
    principal: AppsWriterDep,
    container: ContainerDep,
):
    await container.owner_delegation.revoke_client(
        client_id, actor_id=principal.subject
    )
    return {"revoked": True}


@router.get("/clients")
async def list_app_clients(
    principal: AppsReaderDep,
    container: ContainerDep,
    app_id: str | None = None,
):
    return {"items": await container.app_delegation.list_clients(app_id=app_id)}


@router.post("/clients", status_code=201)
async def create_app_client(
    body: CreateAppClientRequest,
    principal: AppsWriterDep,
    container: ContainerDep,
):
    return await container.app_delegation.create_client(
        **body.model_dump(),
        actor_id=principal.subject,
    )


@router.delete("/clients/{client_id}")
async def revoke_app_client(
    client_id: str,
    principal: AppsWriterDep,
    container: ContainerDep,
):
    await container.app_delegation.revoke_client(client_id, actor_id=principal.subject)
    return {"revoked": True}


@router.post("/clients/{client_id}/rotate-secret")
async def rotate_app_client_secret(
    client_id: str,
    principal: AppsWriterDep,
    container: ContainerDep,
):
    return await container.app_delegation.rotate_client_secret(
        client_id, actor_id=principal.subject
    )


@router.get("")
async def list_all_app_releases(principal: AppsReaderDep, container: ContainerDep):
    rows = await asyncio.to_thread(container.store.list_app_releases)
    return {"items": rows}


@router.get("/{app_id}/releases")
async def list_app_releases(app_id: str, principal: AppsReaderDep, container: ContainerDep):
    rows = await asyncio.to_thread(container.store.list_app_releases, app_id)
    return {"items": rows}


@router.put("/{app_id}/releases/{version}")
async def save_app_release(
    app_id: str,
    version: str,
    body: SaveAppReleaseRequest,
    principal: AppsWriterDep,
    container: ContainerDep,
):
    manifest = dict(body.manifest)
    if manifest.get("app_id") != app_id or str(manifest.get("version") or "") != version:
        raise HTTPException(status_code=400, detail="manifest identity must match the URL")
    return await container.app_releases.save_draft(manifest, actor_id=principal.subject)


@router.post("/{app_id}/releases/{version}/validate")
async def validate_app_release(
    app_id: str,
    version: str,
    principal: AppsWriterDep,
    container: ContainerDep,
):
    return await container.app_releases.validate(
        app_id, version, user_id=_user_id(principal)
    )


@router.post("/{app_id}/releases/{version}/publish")
async def publish_app_release(
    app_id: str,
    version: str,
    principal: AppsPublisherDep,
    container: ContainerDep,
):
    return await container.app_releases.publish(
        app_id,
        version,
        actor_id=principal.subject,
        user_id=_user_id(principal),
    )


@router.get("/installations/mine")
async def list_installations(principal: AppsReaderDep, container: ContainerDep):
    rows = await asyncio.to_thread(
        container.store.list_app_installations, user_id=_user_id(principal)
    )
    return {"items": rows}


@router.post("/{app_id}/install")
async def install_app_release(
    app_id: str,
    body: InstallAppReleaseRequest,
    principal: AppsInstallerDep,
    container: ContainerDep,
):
    return await container.app_releases.install(
        app_id,
        body.version,
        user_id=_user_id(principal),
        actor_id=principal.subject,
        configuration=body.configuration,
        granted_permissions=body.granted_permissions,
    )


@router.post("/installations/{installation_id}/actions")
async def transition_app_installation(
    installation_id: str,
    body: AppInstallationActionRequest,
    principal: AppsInstallerDep,
    container: ContainerDep,
):
    return await container.app_releases.transition(
        installation_id,
        user_id=_user_id(principal),
        actor_id=principal.subject,
        action=body.action,
    )


@router.get("/installations/{installation_id}/events")
async def installation_events(
    installation_id: str,
    principal: AppsReaderDep,
    container: ContainerDep,
):
    rows = await asyncio.to_thread(
        container.store.list_app_installation_events,
        installation_id,
        user_id=_user_id(principal),
    )
    return {"items": rows}
