"""Owner-scoped App data plane backed by the unified Run API."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from joyhousebot.api.app_schemas import (
    CreateAppInstallationAuthorizationRequest,
    RegisterAppCallbackRequest,
)
from joyhousebot.api.dependencies import ContainerDep, ContextDep
from joyhousebot.api.schemas import CreateAppScheduleRequest, ResumeScheduleRequest

router = APIRouter(prefix="/apps", tags=["apps"])


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
    return await container.app_releases.usage(
        installation_id,
        user_id=context.user_id,
        since=since,
        until=until,
    )


@router.post("/{installation_id}/schedules", status_code=201)
async def create_app_schedule(
    installation_id: str,
    body: CreateAppScheduleRequest,
    context: ContextDep,
    container: ContainerDep,
):
    if not context.idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="App schedule creation requires an Idempotency-Key header",
        )
    return await container.schedules.create_app_schedule(
        context,
        body,
        installation_id=installation_id,
        app_releases=container.app_releases,
    )


@router.get("/{installation_id}/schedules")
async def list_app_schedules(
    installation_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    rows = await container.schedules.list_app_schedules(
        context, installation_id=installation_id
    )
    return {"items": rows}


@router.get("/{installation_id}/schedules/{schedule_id}/execution-summary")
async def get_app_schedule_execution_summary(
    installation_id: str,
    schedule_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.schedules.app_execution_summary(
        context,
        installation_id=installation_id,
        schedule_id=schedule_id,
    )


@router.post("/{installation_id}/schedules/{schedule_id}/resume")
async def resume_app_schedule(
    installation_id: str,
    schedule_id: str,
    body: ResumeScheduleRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.schedules.resume_app_schedule(
        context,
        body,
        installation_id=installation_id,
        schedule_id=schedule_id,
    )


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


@router.get("/{installation_id}/authorizations")
async def list_installation_authorizations(
    installation_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    return {
        "items": await container.app_delegation.list_installation_authorizations(
            context, installation_id
        )
    }


@router.put("/{installation_id}/authorizations/{client_id}")
async def authorize_installation(
    installation_id: str,
    client_id: str,
    body: CreateAppInstallationAuthorizationRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.app_delegation.authorize_installation(
        context,
        installation_id,
        client_id=client_id,
        scopes=body.scopes,
        expires_at=body.expires_at.isoformat(),
    )


@router.delete("/{installation_id}/authorizations/{client_id}")
async def revoke_installation_authorization(
    installation_id: str,
    client_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    await container.app_delegation.revoke_installation_authorization(
        context,
        installation_id,
        client_id=client_id,
    )
    return {"revoked": True}


__all__ = ["router"]
