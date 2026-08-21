"""Owner/Installation EntryPoint execution API."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

from fastapi import APIRouter, Header, HTTPException, Query, Response

from joyhousebot.api.dependencies import ContainerDep, PublicContextDep
from joyhousebot.api.public_v2_errors import PUBLIC_ERROR_RESPONSES
from joyhousebot.api.public_v2_pagination import paginate_public_items
from joyhousebot.api.public_v2_presenters import public_run
from joyhousebot.api.public_v2_schemas import (
    CreateEntryPointRunRequest,
    EntryPointDescriptor,
    EntryPointList,
    ExchangeInstallationTokenRequest,
    ExchangeOwnerTokenRequest,
    InstallOwnerAppRequest,
    OwnerAppInstallation,
    OwnerAppInstallationList,
    OwnerTokenResponse,
    PublicRun,
    RefreshOwnerTokenRequest,
)
from joyhousebot.api.run_schemas import CreateRunRequest
from joyhousebot.api.run_submission import submit_create_run
from joyhousebot.application.context import PrincipalKind
from joyhousebot.domain.entrypoints import structured_input_text

router = APIRouter(tags=["public-execution"], responses=PUBLIC_ERROR_RESPONSES)


def _submission_identity(
    entrypoint_id: str, external_key: str, session_id: str | None
) -> tuple[str, str]:
    entrypoint_digest = sha256(entrypoint_id.encode()).hexdigest()[:16]
    internal_key = f"entrypoint:{entrypoint_digest}:{external_key}"
    if session_id:
        return session_id, internal_key
    request_digest = sha256(f"{entrypoint_id}\0{external_key}".encode()).hexdigest()
    return f"entrypoint:{request_digest[:32]}", internal_key


@router.post("/app-auth/token")
async def exchange_installation_token(
    body: ExchangeInstallationTokenRequest,
    container: ContainerDep,
):
    result = await container.app_delegation.exchange_installation(**body.model_dump())
    if result is None:
        raise HTTPException(status_code=401, detail="invalid App installation credentials")
    return result


@router.post("/owner-auth/token", response_model=OwnerTokenResponse)
async def exchange_owner_token(
    body: ExchangeOwnerTokenRequest,
    container: ContainerDep,
):
    result = await container.owner_delegation.exchange(**body.model_dump())
    if result is None:
        raise HTTPException(status_code=401, detail="invalid Owner delegation assertion")
    return result


@router.post("/owner-auth/refresh", response_model=OwnerTokenResponse)
async def refresh_owner_token(
    body: RefreshOwnerTokenRequest,
    container: ContainerDep,
):
    result = await container.owner_delegation.refresh(**body.model_dump())
    if result is None:
        raise HTTPException(status_code=401, detail="invalid Owner refresh credential")
    return result


@router.post("/owner-auth/revoke")
async def revoke_owner_token(
    context: PublicContextDep,
    container: ContainerDep,
):
    await container.owner_delegation.revoke(context)
    return {"revoked": True}


def _require_owner(context: object) -> None:
    principal = getattr(context, "principal", None)
    if getattr(principal, "kind", None) != PrincipalKind.OWNER:
        raise HTTPException(status_code=403, detail="Owner authority required")


def _owner_app(value: dict) -> dict:
    """Project storage-rich installation rows onto the stable public contract."""
    return OwnerAppInstallation.model_validate(
        {name: value.get(name) for name in OwnerAppInstallation.model_fields}
    ).model_dump()


@router.get("/apps", response_model=OwnerAppInstallationList)
async def list_owner_apps(
    context: PublicContextDep,
    container: ContainerDep,
):
    _require_owner(context)
    rows = await container.app_releases.list_installed(user_id=context.user_id, active_only=False)
    return {"items": [_owner_app(item) for item in rows], "next_cursor": None}


@router.post(
    "/apps/{app_id}/install",
    response_model=OwnerAppInstallation,
    status_code=201,
)
async def install_owner_app(
    app_id: str,
    body: InstallOwnerAppRequest,
    context: PublicContextDep,
    container: ContainerDep,
    response: Response,
):
    _require_owner(context)
    installed = await container.app_releases.list_installed(user_id=context.user_id, active_only=False)
    existing = next(
        (
            item
            for item in installed
            if item["app_id"] == app_id and item["version"] == body.version
        ),
        None,
    )
    if existing and existing["status"] == "active":
        response.status_code = 200
        return _owner_app(existing)
    if existing and existing["status"] == "disabled":
        response.status_code = 200
        return _owner_app(
            await container.app_releases.transition(
                existing["installation_id"],
                user_id=context.user_id,
                actor_id=context.principal.subject,
                action="activate",
            )
        )
    release = await container.app_releases.release(app_id, body.version)
    permissions = list(dict(release["manifest"]).get("permissions") or [])
    created = await container.app_releases.install(
        app_id,
        body.version,
        user_id=context.user_id,
        actor_id=context.principal.subject,
        configuration=body.configuration,
        granted_permissions=permissions,
    )
    return _owner_app(
        await container.app_releases.transition(
            created["installation_id"],
            user_id=context.user_id,
            actor_id=context.principal.subject,
            action="activate",
        )
    )


@router.get("/entrypoints", response_model=EntryPointList)
async def list_entrypoints(
    context: PublicContextDep,
    container: ContainerDep,
    limit: int = Query(default=100, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=2048),
):
    rows = await container.app_releases.list_entrypoints(
        user_id=context.user_id,
        installation_id=context.principal.app_installation_id,
    )
    page, next_cursor = paginate_public_items(
        rows,
        key=lambda item: ("", str(item["id"])),
        limit=limit,
        cursor=cursor,
    )
    return {
        "items": page,
        "next_cursor": next_cursor,
    }


@router.get("/entrypoints/{entrypoint_id}", response_model=EntryPointDescriptor)
async def get_entrypoint(
    entrypoint_id: str,
    context: PublicContextDep,
    container: ContainerDep,
):
    rows = await container.app_releases.list_entrypoints(
        user_id=context.user_id,
        installation_id=context.principal.app_installation_id,
    )
    selected = next((item for item in rows if item["id"] == entrypoint_id), None)
    if selected is None:
        raise HTTPException(status_code=404, detail="EntryPoint not found")
    return selected


@router.post(
    "/entrypoints/{entrypoint_id}/runs",
    response_model=PublicRun,
    status_code=202,
)
async def create_entrypoint_run(
    entrypoint_id: str,
    body: CreateEntryPointRunRequest,
    context: PublicContextDep,
    container: ContainerDep,
    response: Response,
    idempotency_key: str | None = Header(default=None),
):
    if idempotency_key and idempotency_key != body.idempotency_key:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key header does not match the request body",
        )
    session_id, internal_key = _submission_identity(
        entrypoint_id, body.idempotency_key, body.session_id
    )
    context = replace(context, idempotency_key=internal_key)
    entrypoint, launch = await container.app_releases.resolve_entrypoint(
        entrypoint_id,
        user_id=context.user_id,
        expected_installation_id=context.principal.app_installation_id,
        structured_input=body.input,
    )
    metadata = dict(launch["metadata"])
    if body.client_context:
        metadata["client_context"] = body.client_context
    policy = dict(launch["entrypoint_policy"])
    request = CreateRunRequest.model_validate(
        {
            "execution": launch["execution"],
            "session_id": session_id,
            "interaction_mode": policy["interaction_mode"],
            "input": {"content": structured_input_text(body.input)},
            "output_schema": policy.get("output_schema"),
            "verification_policy": policy["verification_policy"],
            "timeout_seconds": policy["timeout_seconds"],
            "metadata": metadata,
        }
    )
    record = await submit_create_run(
        request,
        context=context,
        container=container,
        pinned_revision_id=launch.get("pinned_revision_id"),
    )
    expected_prompt = structured_input_text(body.input)
    if str(record.prompt) != expected_prompt:
        raise HTTPException(
            status_code=409,
            detail="idempotency_key was already used with different input",
        )
    response.headers["Location"] = f"/v2/runs/{record.run_id}"
    return public_run(record)


@router.get("/runs/{run_id}", response_model=PublicRun)
async def get_run(
    run_id: str,
    context: PublicContextDep,
    container: ContainerDep,
):
    return public_run(await container.runs.get(context, run_id))


__all__ = ["router"]
