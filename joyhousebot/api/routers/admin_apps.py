"""App Pack catalog, validation, and installation lifecycle endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Header, HTTPException

from joyhousebot.api.app_pack_schemas import (
    AcquireMarketAppRequest,
    AcquisitionActionRequest,
    AppPackActionRequest,
    CreateAppClientRequest,
    InstallAppPackRequest,
    InstallMarketAcquisitionRequest,
    RegisterMarketRequest,
    SaveAppPackRequest,
    SignInstallationReceiptRequest,
    UpdateSubscriptionRequest,
)
from joyhousebot.api.dependencies import (
    AppsInstallerDep,
    AppsPublisherDep,
    AppsReaderDep,
    AppsWriterDep,
    ContainerDep,
)

router = APIRouter(prefix="/admin/apps", tags=["app-packs"])


def _user_id(principal: object) -> str:
    value = str(getattr(principal, "user_id", None) or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="App Pack operation requires a user_id")
    return value


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
async def list_app_packs(principal: AppsReaderDep, container: ContainerDep):
    rows = await asyncio.to_thread(container.store.list_app_releases)
    return {"items": rows}


@router.get("/market/registries")
async def list_market_registries(
    principal: AppsReaderDep, container: ContainerDep
):
    return {"items": await container.app_market.list_registries()}


@router.post("/market/registries")
async def register_market(
    body: RegisterMarketRequest,
    principal: AppsWriterDep,
    container: ContainerDep,
):
    return await container.app_market.register(
        **body.model_dump(),
        actor_id=principal.subject,
    )


@router.post("/market/registries/{registry_id}/installation-key")
async def ensure_market_installation_key(
    registry_id: str,
    principal: AppsInstallerDep,
    container: ContainerDep,
):
    return await container.app_market.ensure_installation_key(
        registry_id, user_id=_user_id(principal)
    )


@router.post("/market/registries/{registry_id}/installation-receipts/sign")
async def sign_market_installation_receipt(
    registry_id: str,
    body: SignInstallationReceiptRequest,
    principal: AppsInstallerDep,
    container: ContainerDep,
):
    return await container.app_market.sign_installation_receipt(
        registry_id,
        user_id=_user_id(principal),
        actor_id=principal.subject,
        value=body.model_dump(),
    )


@router.get("/market/acquisitions")
async def list_market_acquisitions(
    principal: AppsReaderDep, container: ContainerDep
):
    return {
        "items": await container.app_market.list_acquisitions(
            user_id=_user_id(principal)
        )
    }


@router.post("/market/acquisitions")
async def acquire_market_app(
    body: AcquireMarketAppRequest,
    principal: AppsInstallerDep,
    container: ContainerDep,
    idempotency_key: str | None = Header(None),
):
    return await container.app_market.request_acquisition(
        **body.model_dump(),
        request_key=str(idempotency_key or ""),
        user_id=_user_id(principal),
        actor_id=principal.subject,
    )


@router.get("/market/acquisitions/{acquisition_id}")
async def get_market_acquisition(
    acquisition_id: str,
    principal: AppsReaderDep,
    container: ContainerDep,
):
    return await container.app_market.get_acquisition(
        acquisition_id, user_id=_user_id(principal)
    )


@router.get("/market/acquisitions/{acquisition_id}/events")
async def market_acquisition_events(
    acquisition_id: str,
    principal: AppsReaderDep,
    container: ContainerDep,
):
    rows = await asyncio.to_thread(
        container.store.list_app_acquisition_events,
        acquisition_id,
        user_id=_user_id(principal),
    )
    return {"items": rows}


@router.post("/market/acquisitions/{acquisition_id}/actions")
async def act_on_market_acquisition(
    acquisition_id: str,
    body: AcquisitionActionRequest,
    principal: AppsInstallerDep,
    container: ContainerDep,
):
    action = getattr(container.app_market, body.action)
    return await action(
        acquisition_id,
        user_id=_user_id(principal),
        actor_id=principal.subject,
    )


@router.post("/market/acquisitions/{acquisition_id}/install")
async def install_market_acquisition(
    acquisition_id: str,
    body: InstallMarketAcquisitionRequest,
    principal: AppsInstallerDep,
    container: ContainerDep,
):
    return await container.app_market.install_acquisition(
        acquisition_id,
        user_id=_user_id(principal),
        actor_id=principal.subject,
        installation_grant=body.installation_grant,
        configuration=body.configuration,
        granted_permissions=body.granted_permissions,
    )


@router.get("/market/update-subscriptions")
async def list_market_update_subscriptions(
    principal: AppsReaderDep, container: ContainerDep
):
    return {
        "items": await container.app_market.list_update_subscriptions(
            user_id=_user_id(principal)
        )
    }


@router.put("/market/update-subscriptions/{installation_id}")
async def save_market_update_subscription(
    installation_id: str,
    body: UpdateSubscriptionRequest,
    principal: AppsInstallerDep,
    container: ContainerDep,
):
    if body.installation_id != installation_id:
        raise HTTPException(status_code=400, detail="installation identity must match URL")
    return await container.app_market.save_update_subscription(
        **body.model_dump(), user_id=_user_id(principal)
    )


@router.get("/{app_id}/releases")
async def list_app_releases(app_id: str, principal: AppsReaderDep, container: ContainerDep):
    rows = await asyncio.to_thread(container.store.list_app_releases, app_id)
    return {"items": rows}


@router.put("/{app_id}/releases/{version}")
async def save_app_release(
    app_id: str,
    version: str,
    body: SaveAppPackRequest,
    principal: AppsWriterDep,
    container: ContainerDep,
):
    manifest = dict(body.manifest)
    if manifest.get("app_id") != app_id or str(manifest.get("version") or "") != version:
        raise HTTPException(status_code=400, detail="manifest identity must match the URL")
    return await container.app_packs.save_draft(manifest, actor_id=principal.subject)


@router.post("/{app_id}/releases/{version}/validate")
async def validate_app_release(
    app_id: str,
    version: str,
    principal: AppsWriterDep,
    container: ContainerDep,
):
    return await container.app_packs.validate(
        app_id, version, user_id=_user_id(principal)
    )


@router.post("/{app_id}/releases/{version}/publish")
async def publish_app_release(
    app_id: str,
    version: str,
    principal: AppsPublisherDep,
    container: ContainerDep,
):
    return await container.app_packs.publish(
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
async def install_app_pack(
    app_id: str,
    body: InstallAppPackRequest,
    principal: AppsInstallerDep,
    container: ContainerDep,
):
    return await container.app_packs.install(
        app_id,
        body.version,
        user_id=_user_id(principal),
        actor_id=principal.subject,
        configuration=body.configuration,
        granted_permissions=body.granted_permissions,
    )


@router.post("/installations/{installation_id}/actions")
async def transition_app_pack(
    installation_id: str,
    body: AppPackActionRequest,
    principal: AppsInstallerDep,
    container: ContainerDep,
):
    return await container.app_packs.transition(
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
