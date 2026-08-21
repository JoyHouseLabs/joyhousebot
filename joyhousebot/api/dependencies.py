"""FastAPI dependencies for authenticated cloud requests."""

from __future__ import annotations

import asyncio
import hmac
import os
import re
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request
from loguru import logger

from joyhousebot.application.context import Principal, PrincipalKind, RequestContext
from joyhousebot.runtime.tracking import normalize_request_id
from joyhousebot.security.admin_auth import DEFAULT_DEVELOPMENT_ADMIN_USER
from joyhousebot.utils.permissions import permission_granted

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_USER_ID_PATTERN = re.compile(r"^[^\s\x00-\x1f\x7f]{1,128}$")
_SESSION_IMPERSONATION_CONTROL_PREFIXES = (
    "/control/v1/admin",
    "/control/v1/auth",
    "/control/v1/system",
)


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


def _impersonation_reason(value: str | None) -> str:
    reason = str(value or "").strip()
    if not reason:
        raise HTTPException(status_code=403, detail="X-Impersonation-Reason is required")
    if len(reason) > 500:
        raise HTTPException(status_code=400, detail="X-Impersonation-Reason is too long")
    return reason


async def _audit_impersonation(
    request: Request,
    *,
    actor_id: str,
    target_user_id: str,
    reason: str,
) -> None:
    await asyncio.to_thread(
        get_container(request).store.record_operator_impersonation,
        actor_id=actor_id,
        target_user_id=target_user_id,
        reason=reason,
        method=request.method,
        path=request.url.path,
        request_id=str(getattr(request.state, "request_id", "") or "untracked"),
    )


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
    if path.startswith("/v2/"):
        return _required_public_v2_scope(path, operation)
    if path.startswith("/control/v1/"):
        return _required_control_scope(path.removeprefix("/control/v1"), operation)
    if path.startswith("/host/v1/"):
        return f"devices.{operation}"
    if path.startswith("/handoffs/v1/"):
        return f"work_handoffs.{operation}"
    return f"api.{operation}"


def _required_public_v2_scope(path: str, operation: str) -> str:
    if path in {"/v2/app-auth/token", "/v2/owner-auth/token", "/v2/owner-auth/refresh"}:
        return "api.write"
    if path == "/v2/owner-auth/revoke":
        return "runs.write"
    if path == "/v2/apps" or path.startswith("/v2/apps/"):
        return "apps.read" if operation == "read" else "apps.install"
    if path == "/v2/entrypoints" or path.startswith("/v2/entrypoints/"):
        if path.endswith("/runs") and operation == "write":
            return "apps.launch"
        return "apps.read"
    if path == "/v2/runs" or path.startswith("/v2/runs/"):
        return f"runs.{operation}"
    if path == "/v2/artifacts" or path.startswith("/v2/artifacts/"):
        return "runs.read"
    if path == "/v2/approvals" or path.startswith("/v2/approvals/"):
        return f"runs.{operation}"
    return f"api.{operation}"


def _required_control_scope(path: str, operation: str) -> str:
    if path.startswith("/admin"):
        return f"admin.{operation}"
    if path.startswith("/apps/") and path.endswith("/schedules"):
        return "apps.schedules" if operation == "write" else "apps.read"
    if path == "/apps" or path.startswith("/apps/"):
        return f"apps.{operation}"
    if path == "/action-items" or path.startswith("/action-items/"):
        return f"runs.{operation}"
    for namespace in (
        "runs",
        "memory",
        "sessions",
        "schedules",
        "works",
        "workflows",
    ):
        if path == f"/{namespace}" or path.startswith(f"/{namespace}/"):
            return f"{namespace}.{operation}"
    if path == "/work-handoffs" or path.startswith("/work-handoffs/"):
        return f"work_handoffs.{operation}"
    if path == "/event-triggers" or path.startswith("/event-triggers/"):
        return f"automation.{operation}"
    if path == "/event-trigger-deliveries":
        return "automation.read"
    if path.startswith("/system/"):
        return "system.read"
    if path in {"/me", "/agents", "/capabilities", "/scenarios", "/usage"}:
        return "account.read"
    return f"api.{operation}"


def _for_request_surface(request: Request, principal: Principal) -> Principal:
    """Enforce authority classes at the HTTP namespace boundary."""
    path = request.url.path.rstrip("/") or "/"
    if path.startswith("/control/") and principal.kind is not PrincipalKind.OPERATOR:
        raise HTTPException(status_code=403, detail="Operator authority required")
    if principal.token_type == "control" and not path.startswith("/control/"):
        raise HTTPException(
            status_code=403,
            detail="Control credentials are accepted only on the control API",
        )
    return principal


def _enforce_token_scope(access: dict[str, Any], request: Request) -> None:
    required = required_api_scope(request)
    granted = [str(scope) for scope in access.get("scopes") or []]
    if not any(permission_granted(scope, required) for scope in granted):
        raise HTTPException(
            status_code=403,
            detail=f"API token scope required: {required}",
        )


def _api_token_principal_kind(access: dict[str, Any]) -> PrincipalKind:
    try:
        return PrincipalKind(str(access["principal_kind"]))
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=401,
            detail="API token has no valid frozen authority",
        ) from exc


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
    x_impersonation_reason: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[str | None, Header()] = None,
) -> Principal:
    config = get_container(request).config
    gateway = config.gateway
    token = _bearer_token(authorization)

    control_token = str(os.getenv("JOYHOUSEBOT_CONTROL_TOKEN") or "")
    if token and control_token and hmac.compare_digest(token, control_token):
        user_id = _impersonation_target(x_impersonate_user_id)
        reason = _impersonation_reason(x_impersonation_reason) if user_id else ""
        if user_id:
            await _audit_impersonation(
                request,
                actor_id="operator",
                target_user_id=user_id,
                reason=reason,
            )
        # Audit trail for operator impersonation: who is acting as whom, where.
        logger.warning(
            "operator impersonation: subject=operator target_user={} method={} path={}",
            user_id or "(none)",
            request.method,
            request.url.path,
        )
        return _for_request_surface(
            request,
            Principal(
                subject="operator",
                user_id=user_id,
                kind=PrincipalKind.OPERATOR,
                role="operator",
                permissions=("*",),
                token_type="control",
                actor_user_id="operator" if user_id else None,
            )
        )
    access = (
        await asyncio.to_thread(get_container(request).store.authenticate_api_access_token, token)
        if token
        else None
    )
    if access is not None:
        if _impersonation_target(x_impersonate_user_id) is not None:
            raise HTTPException(
                status_code=403,
                detail="API access tokens cannot impersonate another user",
            )
        resolved_user_id = str(access["user_id"])
        admin = await asyncio.to_thread(
            get_container(request).store.get_platform_admin, resolved_user_id
        )
        if admin is not None and admin.enabled:
            principal = _for_request_surface(
                request,
                Principal(
                    subject=f"token:{access['token_id']}",
                    user_id=resolved_user_id,
                    kind=_api_token_principal_kind(access),
                    role=admin.role,
                    permissions=admin.permissions,
                    token_scopes=tuple(str(item) for item in access.get("scopes") or ()),
                    token_type=str(access.get("token_type") or "user"),
                    app_client_id=access.get("app_client_id"),
                    app_grant_id=access.get("delegation_grant_id"),
                    app_installation_id=access.get("app_installation_id"),
                    owner_client_id=access.get("owner_client_id"),
                    owner_delegation_id=access.get("owner_delegation_id"),
                ),
            )
        else:
            principal = _for_request_surface(
                request,
                Principal(
                    subject=f"token:{access['token_id']}",
                    user_id=resolved_user_id,
                    kind=_api_token_principal_kind(access),
                    token_scopes=tuple(str(item) for item in access.get("scopes") or ()),
                    token_type=str(access.get("token_type") or "user"),
                    app_client_id=access.get("app_client_id"),
                    app_grant_id=access.get("delegation_grant_id"),
                    app_installation_id=access.get("app_installation_id"),
                    owner_client_id=access.get("owner_client_id"),
                    owner_delegation_id=access.get("owner_delegation_id"),
                ),
            )
        _enforce_token_scope(access, request)
        return principal

    session = (
        await asyncio.to_thread(get_container(request).store.authenticate_admin_session, token)
        if token
        else None
    )
    if session is not None:
        if session.get("must_change_password") and request.url.path not in {
            "/control/v1/auth/status",
            "/control/v1/auth/password",
            "/control/v1/auth/logout",
        }:
            raise HTTPException(status_code=403, detail="administrator password change required")
        session_user_id = str(session["user_id"])
        permissions = tuple(str(item) for item in session.get("permissions") or ())
        target_user_id = _impersonation_target(x_impersonate_user_id)
        if target_user_id and target_user_id != session_user_id and _is_user_data_request(request):
            role = str(session["role"])
            if not any(permission_granted(grant, "users.impersonate") for grant in permissions):
                raise HTTPException(
                    status_code=403,
                    detail="user impersonation permission required",
                )
            reason = _impersonation_reason(x_impersonation_reason)
            await _audit_impersonation(
                request,
                actor_id=session_user_id,
                target_user_id=target_user_id,
                reason=reason,
            )
            logger.warning(
                "administrator impersonation: actor_user={} target_user={} method={} path={}",
                session_user_id,
                target_user_id,
                request.method,
                request.url.path,
            )
            return _for_request_surface(
                request,
                Principal(
                    subject=f"session:{session['session_id']}",
                    user_id=target_user_id,
                    kind=PrincipalKind.OPERATOR,
                    role=role,
                    permissions=permissions,
                    token_type="browser_session",
                    actor_user_id=session_user_id,
                ),
            )
        return _for_request_surface(
            request,
            Principal(
                subject=f"session:{session['session_id']}",
                user_id=session_user_id,
                kind=PrincipalKind.OPERATOR,
                role=str(session["role"]),
                permissions=permissions,
                token_type="browser_session",
            ),
        )

    # Fail closed: an empty token configuration rejects requests instead of
    # silently trusting caller-supplied identity headers. The insecure dev
    # mode (X-User-Id) requires an explicit allow_insecure_auth=true opt-in.
    if bool(getattr(gateway, "allow_insecure_auth", False)):
        dev_user = str(
            x_user_id or os.getenv("JOYHOUSEBOT_DEV_USER_ID") or DEFAULT_DEVELOPMENT_ADMIN_USER
        ).strip()
        admin = await asyncio.to_thread(get_container(request).store.get_platform_admin, dev_user)
        if admin is not None and admin.enabled:
            return _for_request_surface(
                request,
                Principal(
                    subject=f"dev:{dev_user}",
                    user_id=dev_user,
                    kind=(
                        PrincipalKind.OPERATOR
                        if request.url.path.startswith("/control/")
                        else PrincipalKind.OWNER
                    ),
                    role=admin.role,
                    permissions=admin.permissions,
                ),
            )
        return _for_request_surface(
            request,
            Principal(subject=f"dev:{dev_user}", user_id=dev_user, role="user"),
        )
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


async def get_public_request_context(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    x_request_id: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> RequestContext:
    """Build a context for the Owner/Installation execution surface only."""
    if not principal.is_public_actor:
        raise HTTPException(
            status_code=403,
            detail="Owner or Installation authority required",
        )
    return await get_request_context(
        request,
        principal,
        x_request_id=x_request_id,
        idempotency_key=idempotency_key,
    )


ContainerDep = Annotated[Any, Depends(get_container)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
ContextDep = Annotated[RequestContext, Depends(get_request_context)]
PublicContextDep = Annotated[RequestContext, Depends(get_public_request_context)]


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
    Principal,
    Depends(_permission_dependency("runs.cancel", "run cancellation permission required")),
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
    Principal,
    Depends(_permission_dependency("agents.publish", "Agent publish permission required")),
]
SkillsReaderDep = Annotated[
    Principal, Depends(_permission_dependency("skills.read", "Skill read permission required"))
]
SkillsWriterDep = Annotated[
    Principal, Depends(_permission_dependency("skills.write", "Skill write permission required"))
]
SkillsPublisherDep = Annotated[
    Principal,
    Depends(_permission_dependency("skills.publish", "Skill publish permission required")),
]
PromptsReaderDep = Annotated[
    Principal, Depends(_permission_dependency("prompts.read", "Prompt read permission required"))
]
PromptsWriterDep = Annotated[
    Principal, Depends(_permission_dependency("prompts.write", "Prompt write permission required"))
]
PromptsPublisherDep = Annotated[
    Principal,
    Depends(_permission_dependency("prompts.publish", "Prompt publish permission required")),
]
AppsReaderDep = Annotated[
    Principal, Depends(_permission_dependency("apps.read", "App Package read permission required"))
]
AppsWriterDep = Annotated[
    Principal,
    Depends(_permission_dependency("apps.write", "App Package write permission required")),
]
AppsPublisherDep = Annotated[
    Principal,
    Depends(_permission_dependency("apps.publish", "App Package publish permission required")),
]
AppsInstallerDep = Annotated[
    Principal,
    Depends(_permission_dependency("apps.install", "App Package install permission required")),
]
TeamsReaderDep = Annotated[
    Principal, Depends(_permission_dependency("teams.read", "AgentTeam read permission required"))
]
TeamsWriterDep = Annotated[
    Principal, Depends(_permission_dependency("teams.write", "AgentTeam write permission required"))
]
TeamsPublisherDep = Annotated[
    Principal,
    Depends(_permission_dependency("teams.publish", "AgentTeam publish permission required")),
]
CapabilitiesReaderDep = Annotated[
    Principal,
    Depends(_permission_dependency("capabilities.read", "capability read permission required")),
]
CapabilitiesPublisherDep = Annotated[
    Principal,
    Depends(
        _permission_dependency("capabilities.publish", "capability publish permission required")
    ),
]
SettingsReaderDep = Annotated[
    Principal, Depends(_permission_dependency("settings.read", "settings read permission required"))
]
SettingsWriterDep = Annotated[
    Principal,
    Depends(_permission_dependency("settings.write", "settings write permission required")),
]
AdminsReaderDep = Annotated[
    Principal,
    Depends(_permission_dependency("admins.read", "administrator read permission required")),
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
    Principal,
    Depends(_permission_dependency("scenarios.read", "scenario read permission required")),
]
ScenarioWriterDep = Annotated[
    Principal,
    Depends(_permission_dependency("scenarios.write", "scenario write permission required")),
]
ScenarioPublisherDep = Annotated[
    Principal,
    Depends(_permission_dependency("scenarios.publish", "scenario publish permission required")),
]
EvalsReaderDep = Annotated[
    Principal, Depends(_permission_dependency("evals.read", "evaluation read permission required"))
]
EvalsWriterDep = Annotated[
    Principal,
    Depends(_permission_dependency("evals.write", "evaluation write permission required")),
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
