"""Owner-scoped App data plane backed by the unified Run API."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Response

from porthouse.api.app_pack_schemas import (
    AcquireMarketAppRequest,
    AcquisitionActionRequest,
    CreateAppDelegationGrantRequest,
    InstallMarketAcquisitionRequest,
    LaunchAppRequest,
    RegisterAppCallbackRequest,
    SignInstallationReceiptRequest,
)
from porthouse.api.dependencies import ContainerDep, ContextDep
from porthouse.api.run_schemas import CreateRunRequest
from porthouse.api.run_submission import submit_create_run
from porthouse.api.schemas import CreateAppScheduleRequest
from porthouse.application.presenters import record_dict

router = APIRouter(prefix="/apps", tags=["apps"])


def _require_owner_context(context: ContextDep) -> None:
    if context.principal.app_client_id:
        raise HTTPException(
            status_code=403,
            detail="delegated App credentials cannot manage Market installations",
        )


@router.get("/market/registries")
async def list_owner_market_registries(context: ContextDep, container: ContainerDep):
    _require_owner_context(context)
    rows = await container.app_market.list_registries()
    return {
        "items": [
            {
                "registry_id": item["registry_id"],
                "market_id": item["market_id"],
                "base_url": item["base_url"],
                "status": item["status"],
                "protocol_version": item["protocol_version"],
                "discovery": item["discovery"],
            }
            for item in rows
            if item["status"] == "active"
        ]
    }


@router.post("/market/registries/{registry_id}/installation-key")
async def ensure_owner_market_installation_key(
    registry_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    _require_owner_context(context)
    return await container.app_market.ensure_installation_key(
        registry_id, user_id=context.user_id
    )


@router.post("/market/registries/{registry_id}/installation-receipts/sign")
async def sign_owner_market_installation_receipt(
    registry_id: str,
    body: SignInstallationReceiptRequest,
    context: ContextDep,
    container: ContainerDep,
):
    _require_owner_context(context)
    return await container.app_market.sign_installation_receipt(
        registry_id,
        user_id=context.user_id,
        actor_id=context.principal.subject,
        value=body.model_dump(),
    )


@router.post("/market/acquisitions")
async def acquire_owner_market_app(
    body: AcquireMarketAppRequest,
    context: ContextDep,
    container: ContainerDep,
):
    _require_owner_context(context)
    if not context.idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Market acquisition requires an Idempotency-Key header",
        )
    return await container.app_market.request_acquisition(
        **body.model_dump(),
        request_key=context.idempotency_key,
        user_id=context.user_id,
        actor_id=context.principal.subject,
    )


@router.get("/market/acquisitions/{acquisition_id}")
async def get_owner_market_acquisition(
    acquisition_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    _require_owner_context(context)
    return await container.app_market.get_acquisition(
        acquisition_id, user_id=context.user_id
    )


@router.post("/market/acquisitions/{acquisition_id}/actions")
async def act_on_owner_market_acquisition(
    acquisition_id: str,
    body: AcquisitionActionRequest,
    context: ContextDep,
    container: ContainerDep,
):
    _require_owner_context(context)
    action = getattr(container.app_market, body.action)
    return await action(
        acquisition_id,
        user_id=context.user_id,
        actor_id=context.principal.subject,
    )


@router.post("/market/acquisitions/{acquisition_id}/install")
async def install_owner_market_acquisition(
    acquisition_id: str,
    body: InstallMarketAcquisitionRequest,
    context: ContextDep,
    container: ContainerDep,
):
    _require_owner_context(context)
    return await container.app_market.install_acquisition(
        acquisition_id,
        user_id=context.user_id,
        actor_id=context.principal.subject,
        installation_grant=body.installation_grant,
        configuration=body.configuration,
        granted_permissions=body.granted_permissions,
    )


@router.get("")
async def list_apps(context: ContextDep, container: ContainerDep):
    rows = await container.app_packs.list_installed(
        user_id=context.user_id,
        active_only=True,
    )
    if context.principal.app_installation_id:
        rows = [
            row
            for row in rows
            if row["installation_id"] == context.principal.app_installation_id
        ]
    return {"items": rows}


@router.get("/{installation_id}")
async def get_app(
    installation_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    if (
        context.principal.app_installation_id
        and context.principal.app_installation_id != installation_id
    ):
        raise HTTPException(status_code=404, detail="App installation not found")
    return await container.app_packs.get_installed(
        installation_id,
        user_id=context.user_id,
    )


@router.get("/{installation_id}/usage")
async def get_app_usage(
    installation_id: str,
    context: ContextDep,
    container: ContainerDep,
    since: datetime | None = None,
    until: datetime | None = None,
):
    if context.principal.app_client_id:
        raise HTTPException(status_code=403, detail="delegated App credentials cannot read billing")
    return await container.app_packs.usage(
        installation_id,
        user_id=context.user_id,
        since=since,
        until=until,
    )


@router.post("/{installation_id}/runs", status_code=202)
async def launch_app(
    installation_id: str,
    body: LaunchAppRequest,
    context: ContextDep,
    container: ContainerDep,
    response: Response,
):
    if (
        context.principal.app_installation_id
        and context.principal.app_installation_id != installation_id
    ):
        raise HTTPException(status_code=404, detail="App installation not found")
    if not context.idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="App launch requires an Idempotency-Key header",
        )
    entrypoint, launch = await container.app_packs.resolve_launch(
        installation_id,
        user_id=context.user_id,
        entrypoint_id=body.entrypoint_id,
        scenario_inputs=body.inputs,
    )
    request = CreateRunRequest.model_validate(
        {
            "execution": launch["execution"],
            "session_id": body.session_id,
            "interaction_mode": entrypoint["interaction_mode"],
            "input": body.input.model_dump(),
            "output_schema": entrypoint.get("output_schema"),
            "verification_policy": entrypoint.get("verification_policy") or {},
            "timeout_seconds": entrypoint["timeout_seconds"],
            "metadata": launch["metadata"],
        }
    )
    record = await submit_create_run(
        request,
        context=context,
        container=container,
        pinned_revision_id=launch.get("pinned_revision_id"),
    )
    response.headers["Location"] = f"/v1/runs/{record.run_id}"
    return record_dict(record)


@router.post("/{installation_id}/schedules", status_code=201)
async def create_app_schedule(
    installation_id: str,
    body: CreateAppScheduleRequest,
    context: ContextDep,
    container: ContainerDep,
):
    if (
        context.principal.app_installation_id
        and context.principal.app_installation_id != installation_id
    ):
        raise HTTPException(status_code=404, detail="App installation not found")
    if not context.idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="App schedule creation requires an Idempotency-Key header",
        )
    row = await container.schedules.create_app_schedule(
        context,
        body,
        installation_id=installation_id,
        app_packs=container.app_packs,
    )
    return row


@router.get("/{installation_id}/schedules")
async def list_app_schedules(
    installation_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    if (
        context.principal.app_installation_id
        and context.principal.app_installation_id != installation_id
    ):
        raise HTTPException(status_code=404, detail="App installation not found")
    rows = await container.schedules.list_app_schedules(
        context, installation_id=installation_id
    )
    return {"items": rows}


@router.get("/{installation_id}/callbacks")
async def list_app_callbacks(
    installation_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    return {
        "items": await container.app_callbacks.list(context, installation_id)
    }


@router.post("/{installation_id}/callbacks", status_code=201)
async def register_app_callback(
    installation_id: str,
    body: RegisterAppCallbackRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.app_callbacks.register(
        context,
        installation_id,
        body.model_dump(),
    )


@router.delete("/{installation_id}/callbacks/{callback_id}")
async def revoke_app_callback(
    installation_id: str,
    callback_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    await container.app_callbacks.revoke(context, installation_id, callback_id)
    return {"revoked": True}


@router.get("/{installation_id}/delegations")
async def list_app_delegations(
    installation_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    return {
        "items": await container.app_delegation.list_grants(context, installation_id)
    }


@router.post("/{installation_id}/delegations", status_code=201)
async def authorize_app_delegation(
    installation_id: str,
    body: CreateAppDelegationGrantRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.app_delegation.authorize(
        context,
        installation_id,
        client_id=body.client_id,
        scopes=body.scopes,
        expires_at=body.expires_at.isoformat(),
    )


@router.delete("/delegations/{grant_id}")
async def revoke_app_delegation(
    grant_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    await container.app_delegation.revoke_grant(context, grant_id)
    return {"revoked": True}


__all__ = ["router"]
