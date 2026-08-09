"""FastAPI dependencies for authenticated cloud requests."""

from __future__ import annotations

import asyncio
import hmac
import os
import re
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request
from loguru import logger

from joyhousebot.application.context import Principal, RequestContext
from joyhousebot.runtime.tracking import normalize_request_id
from joyhousebot.security.admin_auth import DEFAULT_DEVELOPMENT_ADMIN_USER
from joyhousebot.utils.permissions import permission_granted

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_USER_ID_PATTERN = re.compile(r"^[^\s\x00-\x1f\x7f]{1,128}$")
_SESSION_IMPERSONATION_CONTROL_PREFIXES = ("/v1/admin", "/v1/auth", "/v1/system")


def _impersonation_target(value: str | None) -> str | None:
    target = str(value or "").strip()
    if not target:
        return None
    if not _USER_ID_PATTERN.fullmatch(target):
        raise HTTPException(
            status_code=400,
            detail="X-Impersonate-User-ID must be 1-128 characters without whitespace",
        )
    return target


def _is_user_data_request(request: Request) -> bool:
    """Keep administrator/control APIs bound to the authenticated admin."""
    path = request.url.path.rstrip("/") or "/"
    return not any(
        path == prefix or path.startswith(f"{prefix}/")
        for prefix in _SESSION_IMPERSONATION_CONTROL_PREFIXES
    )


def required_api_scope(request: Request) -> str:
    """Map an HTTP operation to a stable, low-cardinality token scope."""
    path = request.url.path.rstrip("/") or "/"
    operation = "read" if request.method.upper() in _READ_METHODS else "write"
    if path.startswith("/v1/admin"):
        return f"admin.{operation}"
    for namespace in ("runs", "memory", "sessions", "schedules", "works", "workflows"):
        if path == f"/v1/{namespace}" or path.startswith(f"/v1/{namespace}/"):
            return f"{namespace}.{operation}"
    if path == "/v1/event-triggers" or path.startswith("/v1/event-triggers/"):
        return f"automation.{operation}"
    if path == "/v1/event-trigger-deliveries":
        return "automation.read"
    if path.startswith("/v1/system/"):
        return "system.read"
    if path in {"/v1/me", "/v1/agents", "/v1/capabilities", "/v1/scenarios", "/v1/usage"}:
        return "account.read"
    return f"api.{operation}"


def _enforce_token_scope(access: dict[str, Any], request: Request) -> None:
    required = required_api_scope(request)
    granted = [str(scope) for scope in access.get("scopes") or []]
    if not any(permission_granted(scope, required) for scope in granted):
        raise HTTPException(
            status_code=403,
            detail=f"API token scope required: {required}",
        )


def get_container(request: Request) -> Any:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError("application container is not initialized")
    return container


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


async def get_principal(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_impersonate_user_id: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[str | None, Header()] = None,
) -> Principal:
    config = get_container(request).config
    gateway = config.gateway
    token = _bearer_token(authorization)

    control_token = str(os.getenv("JOYHOUSEBOT_CONTROL_TOKEN") or "")
    if token and control_token and hmac.compare_digest(token, control_token):
        user_id = _impersonation_target(x_impersonate_user_id)
        # Audit trail for operator impersonation: who is acting as whom, where.
        logger.warning(
            "operator impersonation: subject=operator target_user={} method={} path={}",
            user_id or "(none)",
            request.method,
            request.url.path,
        )
        return Principal(
            subject="operator",
            user_id=user_id,
            role="operator",
            permissions=("*",),
            actor_user_id="operator" if user_id else None,
        )
    access = (
        await asyncio.to_thread(
            get_container(request).store.authenticate_api_access_token, token
        )
        if token
        else None
    )
    if access is not None:
        if _impersonation_target(x_impersonate_user_id) is not None:
            raise HTTPException(
                status_code=403,
                detail="API access tokens cannot impersonate another user",
            )
        _enforce_token_scope(access, request)
        resolved_user_id = str(access["user_id"])
        admin = await asyncio.to_thread(
            get_container(request).store.get_platform_admin, resolved_user_id
        )
        if admin is not None and admin.enabled:
            return Principal(
                subject=f"token:{access['token_id']}",
                user_id=resolved_user_id,
                role=admin.role,
                permissions=admin.permissions,
                token_scopes=tuple(str(item) for item in access.get("scopes") or ()),
                token_type=str(access.get("token_type") or "user"),
            )
        return Principal(
            subject=f"token:{access['token_id']}",
            user_id=resolved_user_id,
            token_scopes=tuple(str(item) for item in access.get("scopes") or ()),
            token_type=str(access.get("token_type") or "user"),
        )

    session = (
        await asyncio.to_thread(
            get_container(request).store.authenticate_admin_session, token
        )
        if token
        else None
    )
    if session is not None:
        if session.get("must_change_password") and request.url.path not in {
            "/v1/auth/status",
            "/v1/auth/password",
            "/v1/auth/logout",
        }:
            raise HTTPException(status_code=403, detail="administrator password change required")
        session_user_id = str(session["user_id"])
        permissions = tuple(str(item) for item in session.get("permissions") or ())
        target_user_id = _impersonation_target(x_impersonate_user_id)
        if (
            target_user_id
            and target_user_id != session_user_id
            and _is_user_data_request(request)
        ):
            role = str(session["role"])
            if role != "operator" and not any(
                permission_granted(grant, "users.impersonate") for grant in permissions
            ):
                raise HTTPException(
                    status_code=403,
                    detail="user impersonation permission required",
                )
            logger.warning(
                "administrator impersonation: actor_user={} target_user={} method={} path={}",
                session_user_id,
                target_user_id,
                request.method,
                request.url.path,
            )
            return Principal(
                subject=f"session:{session['session_id']}",
                user_id=target_user_id,
                role=role,
                permissions=permissions,
                token_type="browser_session",
                actor_user_id=session_user_id,
            )
        return Principal(
            subject=f"session:{session['session_id']}",
            user_id=session_user_id,
            role=str(session["role"]),
            permissions=permissions,
            token_type="browser_session",
        )

    # Fail closed: an empty token configuration rejects requests instead of
    # silently trusting caller-supplied identity headers. The insecure dev
    # mode (X-User-Id) requires an explicit allow_insecure_auth=true opt-in.
    if bool(getattr(gateway, "allow_insecure_auth", False)):
        dev_user = str(
            x_user_id
            or os.getenv("JOYHOUSEBOT_DEV_USER_ID")
            or DEFAULT_DEVELOPMENT_ADMIN_USER
        ).strip()
        admin = await asyncio.to_thread(
            get_container(request).store.get_platform_admin, dev_user
        )
        if admin is not None and admin.enabled:
            return Principal(
                subject=f"dev:{dev_user}",
                user_id=dev_user,
                role=admin.role,
                permissions=admin.permissions,
            )
        return Principal(subject=f"dev:{dev_user}", user_id=dev_user, role="user")
    raise HTTPException(status_code=401, detail="invalid or missing bearer token")


async def get_request_context(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    x_request_id: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> RequestContext:
    if not principal.user_id:
        raise HTTPException(
            status_code=400,
            detail="operator requests require X-Impersonate-User-ID",
        )
    request_id = normalize_request_id(
        getattr(request.state, "request_id", None) or x_request_id,
        prefix="req",
    )
    tracker_id = normalize_request_id(
        getattr(request.state, "tracker_id", None) or request_id,
        prefix="trace",
    )
    carrier = dict(getattr(request.state, "trace_carrier", {}) or {})
    return RequestContext(
        principal=principal,
        request_id=request_id,
        idempotency_key=str(idempotency_key or "").strip() or None,
        tracker_id=tracker_id,
        traceparent=str(carrier.get("traceparent") or "") or None,
        tracestate=str(carrier.get("tracestate") or "") or None,
    )


ContainerDep = Annotated[Any, Depends(get_container)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
ContextDep = Annotated[RequestContext, Depends(get_request_context)]


async def require_scenario_editor(principal: PrincipalDep) -> Principal:
    if not principal.can("scenarios.write"):
        raise HTTPException(status_code=403, detail="scenario editor permission required")
    return principal


ScenarioEditorDep = Annotated[Principal, Depends(require_scenario_editor)]


def _permission_dependency(permission: str, detail: str):
    async def dependency(principal: PrincipalDep) -> Principal:
        if not principal.can(permission):
            raise HTTPException(status_code=403, detail=detail)
        return principal

    return dependency


async def require_platform_admin(principal: PrincipalDep) -> Principal:
    if not principal.can("platform.read"):
        raise HTTPException(status_code=403, detail="platform administrator permission required")
    return principal


PlatformAdminDep = Annotated[Principal, Depends(require_platform_admin)]


async def require_admin_writer(principal: PrincipalDep) -> Principal:
    if not principal.can("admins.write"):
        raise HTTPException(status_code=403, detail="administrator management permission required")
    return principal


AdminWriterDep = Annotated[Principal, Depends(require_admin_writer)]

RunsReaderDep = Annotated[
    Principal, Depends(_permission_dependency("runs.read", "run read permission required"))
]
RunsCancellerDep = Annotated[
    Principal, Depends(_permission_dependency("runs.cancel", "run cancellation permission required"))
]
WorkersReaderDep = Annotated[
    Principal, Depends(_permission_dependency("workers.read", "worker read permission required"))
]
AgentsReaderDep = Annotated[
    Principal, Depends(_permission_dependency("agents.read", "Agent read permission required"))
]
AgentsWriterDep = Annotated[
    Principal, Depends(_permission_dependency("agents.write", "Agent write permission required"))
]
AgentsPublisherDep = Annotated[
    Principal, Depends(_permission_dependency("agents.publish", "Agent publish permission required"))
]
CapabilitiesReaderDep = Annotated[
    Principal,
    Depends(_permission_dependency("capabilities.read", "capability read permission required")),
]
CapabilitiesPublisherDep = Annotated[
    Principal,
    Depends(_permission_dependency("capabilities.publish", "capability publish permission required")),
]
SettingsReaderDep = Annotated[
    Principal, Depends(_permission_dependency("settings.read", "settings read permission required"))
]
SettingsWriterDep = Annotated[
    Principal, Depends(_permission_dependency("settings.write", "settings write permission required"))
]
AdminsReaderDep = Annotated[
    Principal, Depends(_permission_dependency("admins.read", "administrator read permission required"))
]
TokensReaderDep = Annotated[
    Principal, Depends(_permission_dependency("tokens.read", "token read permission required"))
]
TokensWriterDep = Annotated[
    Principal, Depends(_permission_dependency("tokens.write", "token write permission required"))
]
AuditReaderDep = Annotated[
    Principal, Depends(_permission_dependency("audit.read", "audit read permission required"))
]
RolloutsReaderDep = Annotated[
    Principal, Depends(_permission_dependency("rollouts.read", "rollout read permission required"))
]
RolloutsWriterDep = Annotated[
    Principal,
    Depends(_permission_dependency("rollouts.write", "rollout write permission required")),
]
ScenarioReaderDep = Annotated[
    Principal, Depends(_permission_dependency("scenarios.read", "scenario read permission required"))
]
ScenarioWriterDep = Annotated[
    Principal, Depends(_permission_dependency("scenarios.write", "scenario write permission required"))
]
ScenarioPublisherDep = Annotated[
    Principal, Depends(_permission_dependency("scenarios.publish", "scenario publish permission required"))
]
EvalsReaderDep = Annotated[
    Principal, Depends(_permission_dependency("evals.read", "evaluation read permission required"))
]
EvalsWriterDep = Annotated[
    Principal, Depends(_permission_dependency("evals.write", "evaluation write permission required"))
]


async def require_reasoning_reader(principal: PrincipalDep) -> Principal:
    if not principal.can("runs.read") or not principal.can("reasoning.read"):
        raise HTTPException(status_code=403, detail="reasoning access permission required")
    return principal


ReasoningReaderDep = Annotated[Principal, Depends(require_reasoning_reader)]


async def require_raw_trace_reader(principal: PrincipalDep) -> Principal:
    if not principal.can("runs.read") or not principal.can("reasoning.read_raw"):
        raise HTTPException(status_code=403, detail="raw trace access permission required")
    return principal


RawTraceReaderDep = Annotated[Principal, Depends(require_raw_trace_reader)]


async def require_replay_writer(principal: PrincipalDep) -> Principal:
    if not principal.can("runs.read") or not principal.can("replay.execute"):
        raise HTTPException(status_code=403, detail="replay execution permission required")
    return principal


ReplayWriterDep = Annotated[Principal, Depends(require_replay_writer)]

ReplayReaderDep = Annotated[
    Principal, Depends(_permission_dependency("replay.read", "replay read permission required"))
]
