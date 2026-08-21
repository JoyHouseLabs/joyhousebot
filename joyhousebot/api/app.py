"""Small FastAPI composition root for the public cloud API."""

from __future__ import annotations

import asyncio
import hmac
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from joyhousebot.api.dependencies import _bearer_token
from joyhousebot.api.lifecycle import annotate_api_lifecycle
from joyhousebot.api.mcp_gateway import MCPGateway
from joyhousebot.api.public_v2_errors import is_public_v2_path, public_error_response
from joyhousebot.api.public_v2_openapi import public_v2_openapi_document
from joyhousebot.api.rate_limit import RateLimitMiddleware
from joyhousebot.api.routers import (
    action_items,
    admin_apps,
    admin_catalog,
    admin_embedding_profiles,
    admin_evals,
    admin_experiments,
    admin_extensions,
    admin_model_providers,
    admin_platform,
    admin_prompts,
    admin_remote_connections,
    admin_scenarios,
    admin_skills,
    admin_teams,
    apps,
    artifact_uploads,
    auth,
    device_hosts,
    event_triggers,
    graph_events,
    host_tools,
    input_assets,
    knowledge,
    memory,
    model_grants,
    public_execution_v2,
    public_run_resources_v2,
    runs,
    schedules,
    sessions,
    system,
    workflows,
    works,
)
from joyhousebot.application.errors import (
    ApplicationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from joyhousebot.bootstrap.container import ApplicationContainer, build_api_container
from joyhousebot.config.access import get_config
from joyhousebot.observability.otel import configure_telemetry, current_trace_carrier
from joyhousebot.observability.prometheus import render_prometheus
from joyhousebot.runtime.tracking import normalize_request_id

_PRODUCTION_ENVIRONMENTS = {"prod", "production"}


def _production_environment() -> bool:
    return str(os.getenv("JOYHOUSEBOT_ENVIRONMENT") or "development").strip().lower() in (
        _PRODUCTION_ENVIRONMENTS
    )


def validate_deployment_security(
    *, surface: Literal["combined", "public", "control"], config: object | None = None
) -> None:
    """Reject production combinations that collapse a security boundary."""
    if not _production_environment():
        return
    if surface == "combined" and str(
        os.getenv("JOYHOUSEBOT_ALLOW_COMBINED_SURFACE") or ""
    ).strip().lower() not in {"1", "true", "yes"}:
        raise ValueError(
            "production requires separate public and control API surfaces; "
            "set JOYHOUSEBOT_API_SURFACE explicitly"
        )
    control_token = str(os.getenv("JOYHOUSEBOT_CONTROL_TOKEN") or "")
    if control_token and len(control_token) < 32:
        raise ValueError("JOYHOUSEBOT_CONTROL_TOKEN must contain at least 32 characters")
    metrics_token = str(os.getenv("JOYHOUSEBOT_METRICS_TOKEN") or "")
    if metrics_token and len(metrics_token) < 32:
        raise ValueError("JOYHOUSEBOT_METRICS_TOKEN must contain at least 32 characters")
    if config is None:
        return
    gateway = getattr(config, "gateway", None)
    if bool(getattr(gateway, "allow_insecure_auth", False)):
        raise ValueError("allow_insecure_auth cannot be enabled in production")
    if "*" in set(getattr(gateway, "cors_origins", []) or []):
        raise ValueError("wildcard CORS origins are forbidden in production")


class SPAStaticFiles(StaticFiles):
    """Serve the Vue history-mode entry point for non-asset UI routes."""

    async def get_response(self, path: str, scope: dict) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and "." not in Path(path).name:
                return await super().get_response("index.html", scope)
            raise
        if response.status_code == 404 and "." not in Path(path).name:
            return await super().get_response("index.html", scope)
        return response


def _cors_origins(injected: ApplicationContainer | None) -> list[str]:
    """Resolve CORS origins without breaking app import when config is absent.

    When the container is injected (tests, embedding) its config wins. For the
    module-level app the deployment config is loaded lazily; if it cannot be
    loaded, lifespan will fail the same way later, so falling back to schema
    defaults here only affects import time.
    """
    if injected is not None:
        gateway = injected.config.gateway
    else:
        try:
            gateway = get_config().gateway
        except Exception:
            from joyhousebot.config.schema import Config

            gateway = Config().gateway
    return list(getattr(gateway, "cors_origins", []) or [])


def _app_lifespan(
    injected: ApplicationContainer | None,
    surface: Literal["combined", "public", "control"],
    mcp_gateway: MCPGateway,
):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active = injected or build_api_container()
        validate_deployment_security(surface=surface, config=active.config)
        app.state.container = active
        await mcp_gateway.configure(active)
        gateway = active.config.gateway
        if getattr(gateway, "allow_insecure_auth", False):
            logger.warning(
                "*** INSECURE DEV MODE: allow_insecure_auth=true — requests are "
                "authenticated by the X-User-Id header only. Never expose this "
                "deployment; issue database-backed API tokens for cloud use."
            )
        try:
            # FastMCP's mounted ASGI app does not receive the parent FastAPI
            # lifespan automatically.  Keep its streamable-http task group
            # alive for the same process lifetime, otherwise every MCP request
            # fails with "Task group is not initialized".
            async with mcp_gateway.server.session_manager.run():
                yield
        finally:
            await mcp_gateway.close()
            await active.close()

    return lifespan


def _configure_middleware(app: FastAPI, injected: ApplicationContainer | None) -> None:
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(injected),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Content-SHA256",
            "Prefer",
            "X-Request-Id",
            "X-Tracker-Id",
            "X-Impersonate-User-ID",
            "X-JoyHouseBot-Event-Token",
            "X-JoyHouseBot-Device-ID",
            "X-JoyHouseBot-Webhook-Secret",
            "X-User-Id",
        ],
        expose_headers=["Location", "Preference-Applied", "X-Request-Id", "X-Tracker-Id"],
    )


def _register_request_tracking(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_tracking(request: Request, call_next):
        request_id = normalize_request_id(request.headers.get("x-request-id"), prefix="req")
        tracker_id = normalize_request_id(
            request.headers.get("x-tracker-id") or request_id, prefix="trace"
        )
        request.state.request_id = request_id
        request.state.tracker_id = tracker_id
        request.state.trace_carrier = current_trace_carrier()
        started = time.monotonic()
        with logger.contextualize(request_id=request_id, tracker_id=tracker_id):
            try:
                response = await call_next(request)
            except BaseException:
                logger.exception("API request failed: {} {}", request.method, request.url.path)
                raise
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "API request completed: {} {} status={} duration_ms={}",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Tracker-Id"] = tracker_id
        return response


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if not is_public_v2_path(request.url.path):
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        code = {
            400: "invalid_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            422: "invalid_request",
            429: "rate_limited",
        }.get(exc.status_code, "http_error")
        return public_error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        if not is_public_v2_path(request.url.path):
            return JSONResponse(
                status_code=422,
                content={"detail": jsonable_encoder(exc.errors())},
            )
        first = exc.errors()[0] if exc.errors() else {}
        location = [str(item) for item in first.get("loc") or () if item != "body"]
        return public_error_response(
            422,
            "invalid_request",
            str(first.get("msg") or "request validation failed"),
            retryable=False,
            field_path=".".join(location) or None,
        )

    @app.exception_handler(ApplicationError)
    async def application_error_handler(request: Request, exc: ApplicationError) -> JSONResponse:
        status = 400
        if isinstance(exc, NotFoundError):
            status = 404
        elif isinstance(exc, ConflictError):
            status = 409
        elif isinstance(exc, AuthorizationError):
            status = 403
        elif isinstance(exc, ValidationError):
            status = 422
        if is_public_v2_path(request.url.path):
            return public_error_response(status, exc.code, str(exc))
        return JSONResponse(
            status_code=status, content={"error": {"code": exc.code, "message": str(exc)}}
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        if is_public_v2_path(request.url.path):
            return public_error_response(422, "invalid_request", str(exc), retryable=False)
        return JSONResponse(
            status_code=422, content={"error": {"code": "invalid_request", "message": str(exc)}}
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled API error: {} {}", request.method, request.url.path)
        if is_public_v2_path(request.url.path):
            return public_error_response(500, "internal_error", "internal server error")
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "internal server error"}},
        )


async def _cached_operational_metrics(request: Request) -> dict:
    cache = request.app.state.metrics_cache
    async with cache["lock"]:
        now = time.monotonic()
        if cache["data"] is None or now >= cache["expires_at"]:
            cache["data"] = await asyncio.to_thread(
                request.app.state.container.store.operational_metrics
            )
            cache["expires_at"] = now + 5.0
        return cache["data"]


def _register_probes(app: FastAPI) -> None:
    @app.get("/healthz", tags=["system"])
    async def healthz():
        return {"ok": True, "service": "joyhousebot-api"}

    @app.get("/readyz", tags=["system"])
    async def readyz(request: Request):
        # Public probe: expose only a boolean. Detailed health data lives
        # behind the authenticated /control/v1/system/health endpoint.
        result = await asyncio.to_thread(request.app.state.container.store.healthcheck)
        ok = bool(result.get("ok"))
        return JSONResponse(status_code=200 if ok else 503, content={"ok": ok})

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request):
        metrics_token = str(os.getenv("JOYHOUSEBOT_METRICS_TOKEN") or "").strip()
        if not metrics_token:
            # Fail closed: without an explicit scrape token the endpoint is
            # disabled rather than exposed on the data plane.
            return Response(status_code=404, content="metrics endpoint disabled\n")
        if not hmac.compare_digest(
            _bearer_token(request.headers.get("authorization")), metrics_token
        ):
            return Response(status_code=401, content="metrics authentication required\n")
        try:
            data = await _cached_operational_metrics(request)
        except Exception:
            # Keep the scrape endpoint observable during a database outage. The
            # process is alive, but the data plane is not ready.
            logger.exception("failed to collect operational metrics")
            return Response(
                content="# HELP joyhousebot_up API process readiness.\n"
                "# TYPE joyhousebot_up gauge\njoyhousebot_up 0\n",
                status_code=503,
                media_type="text/plain; version=0.0.4",
            )
        return Response(content=render_prometheus(data), media_type="text/plain; version=0.0.4")


def _register_routers(
    app: FastAPI,
    *,
    surface: Literal["combined", "public", "control"],
    mcp_gateway: MCPGateway,
) -> None:
    control_prefix = "/control/v1"
    host_prefix = "/host/v1"
    event_prefix = "/events/v1"
    if surface in {"combined", "control"}:
        for control_router in (
            system.router,
            auth.router,
            admin_platform.router,
            admin_apps.router,
            admin_teams.router,
            admin_catalog.router,
            admin_evals.router,
            admin_model_providers.router,
            admin_embedding_profiles.router,
            admin_experiments.router,
            admin_extensions.router,
            admin_prompts.router,
            admin_remote_connections.router,
            admin_scenarios.router,
            admin_skills.router,
            event_triggers.control_router,
            apps.router,
            action_items.router,
            runs.router,
            input_assets.router,
            knowledge.router,
            memory.router,
            sessions.router,
            schedules.router,
            works.router,
            workflows.router,
        ):
            app.include_router(control_router, prefix=control_prefix)
    # Host and inbound-event protocols are separate from both the public App
    # execution contract and the operator control API.  They remain available
    # on either deployable surface so a split deployment can route them
    # independently without restoring the legacy catch-all /v1 namespace.
    for host_router in (
        artifact_uploads.router,
        device_hosts.router,
        host_tools.router,
        model_grants.router,
    ):
        app.include_router(host_router, prefix=host_prefix)
    app.include_router(graph_events.router, prefix=event_prefix)
    app.include_router(event_triggers.receiver_router, prefix=event_prefix)
    if surface in {"combined", "public"}:
        app.include_router(public_execution_v2.router, prefix="/v2")
        app.include_router(public_run_resources_v2.router, prefix="/v2")
        app.include_router(works.handoff_router, prefix="/handoffs/v1")
        app.include_router(works.share_router, prefix="/shares/v1")
        app.mount("/mcp", mcp_gateway.asgi_app, name="mcp")


def _mount_control_ui(app: FastAPI, surface: Literal["combined", "public", "control"]) -> None:
    ui_dir = Path(__file__).resolve().parent.parent / "static" / "ui"
    if surface in {"combined", "control"} and ui_dir.exists():
        app.mount("/ui", SPAStaticFiles(directory=str(ui_dir), html=True), name="ui")


def _restrict_public_openapi(app: FastAPI, surface: Literal["combined", "public", "control"]) -> None:
    """Publish only the stable Owner/Installation contract on the public surface.

    Host and inbound-event protocols remain routable for their dedicated,
    authenticated clients, but they are not part of the ordinary App contract
    advertised at ``/openapi.json``.
    """
    if surface != "public":
        return
    unfiltered_openapi = app.openapi

    def stable_public_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = public_v2_openapi_document(unfiltered_openapi())
        return app.openapi_schema

    app.openapi = stable_public_openapi


def create_app(
    container: ApplicationContainer | None = None,
    *,
    surface: Literal["combined", "public", "control"] = "combined",
) -> FastAPI:
    validate_deployment_security(
        surface=surface,
        config=getattr(container, "config", None),
    )
    mcp_gateway = MCPGateway(cors_origins=_cors_origins(container))
    app = FastAPI(
        title=f"joyhousebot {surface.title()} API",
        version="2.0.0",
        description="Multi-user distributed Agent runtime",
        lifespan=_app_lifespan(container, surface, mcp_gateway),
    )
    app.state.metrics_cache = {"expires_at": 0.0, "data": None, "lock": asyncio.Lock()}
    app.state.surface = surface
    app.state.mcp_gateway = mcp_gateway
    _configure_middleware(app, container)
    _register_request_tracking(app)
    _register_error_handlers(app)
    _register_probes(app)
    _register_routers(app, surface=surface, mcp_gateway=mcp_gateway)
    annotate_api_lifecycle(app)
    _mount_control_ui(app, surface)
    _restrict_public_openapi(app, surface)
    configure_telemetry(service_name=f"joyhousebot-api-{surface}", app=app)
    return app


_surface = os.getenv("JOYHOUSEBOT_API_SURFACE", "combined").strip().lower()
if _surface not in {"combined", "public", "control"}:
    raise ValueError("JOYHOUSEBOT_API_SURFACE must be combined, public, or control")
app = create_app(surface=_surface)
