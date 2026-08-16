"""Small FastAPI composition root for the public cloud API."""

from __future__ import annotations

import asyncio
import hmac
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from porthouse.api.dependencies import _bearer_token
from porthouse.api.mcp_gateway import MCPGateway
from porthouse.api.rate_limit import RateLimitMiddleware
from porthouse.api.routers import (
    action_items,
    admin_apps,
    admin_catalog,
    admin_embedding_profiles,
    admin_evals,
    admin_experiments,
    admin_model_providers,
    admin_platform,
    admin_plugins,
    admin_prompts,
    admin_remote_connections,
    admin_scenarios,
    admin_skills,
    admin_teams,
    app_auth,
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
    runs,
    schedules,
    sessions,
    system,
    workflows,
    works,
)
from porthouse.application.errors import (
    ApplicationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from porthouse.bootstrap.container import ApplicationContainer, build_api_container
from porthouse.config.access import get_config
from porthouse.observability.otel import configure_telemetry, current_trace_carrier
from porthouse.observability.prometheus import render_prometheus
from porthouse.runtime.tracking import normalize_request_id

_PRODUCTION_ENVIRONMENTS = {"prod", "production"}


def _production_environment() -> bool:
    return str(os.getenv("PORTHOUSE_ENVIRONMENT") or "development").strip().lower() in (
        _PRODUCTION_ENVIRONMENTS
    )


def validate_deployment_security(
    *, surface: Literal["combined", "public", "control"], config: object | None = None
) -> None:
    """Reject production combinations that collapse a security boundary."""
    if not _production_environment():
        return
    if surface == "combined" and str(
        os.getenv("PORTHOUSE_ALLOW_COMBINED_SURFACE") or ""
    ).strip().lower() not in {"1", "true", "yes"}:
        raise ValueError(
            "production requires separate public and control API surfaces; "
            "set PORTHOUSE_API_SURFACE explicitly"
        )
    control_token = str(os.getenv("PORTHOUSE_CONTROL_TOKEN") or "")
    if control_token and len(control_token) < 32:
        raise ValueError("PORTHOUSE_CONTROL_TOKEN must contain at least 32 characters")
    metrics_token = str(os.getenv("PORTHOUSE_METRICS_TOKEN") or "")
    if metrics_token and len(metrics_token) < 32:
        raise ValueError("PORTHOUSE_METRICS_TOKEN must contain at least 32 characters")
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
            from porthouse.config.schema import Config

            gateway = Config().gateway
    return list(getattr(gateway, "cors_origins", []) or [])


def create_app(
    container: ApplicationContainer | None = None,
    *,
    surface: Literal["combined", "public", "control"] = "combined",
) -> FastAPI:
    injected = container
    validate_deployment_security(
        surface=surface,
        config=getattr(injected, "config", None),
    )

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

    app = FastAPI(
        title=f"Porthouse {surface.title()} API",
        version="1.0.0",
        description="Multi-user distributed Agent runtime",
        lifespan=lifespan,
    )
    app.state.metrics_cache = {"expires_at": 0.0, "data": None, "lock": asyncio.Lock()}
    app.state.surface = surface
    mcp_gateway = MCPGateway(cors_origins=_cors_origins(injected))
    app.state.mcp_gateway = mcp_gateway
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
            "X-Porthouse-Event-Token",
            "X-Porthouse-Device-ID",
            "X-Porthouse-Webhook-Secret",
            "X-User-Id",
        ],
        expose_headers=["Location", "Preference-Applied", "X-Request-Id", "X-Tracker-Id"],
    )

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
        return JSONResponse(
            status_code=status,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_request", "message": str(exc)}},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled API error: {} {}", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "internal server error"}},
        )

    @app.get("/healthz", tags=["system"])
    async def healthz():
        return {"ok": True, "service": "porthouse-api"}

    @app.get("/readyz", tags=["system"])
    async def readyz(request: Request):
        # Public probe: expose only a boolean. Detailed health data lives
        # behind the authenticated /v1/system/health endpoint.
        result = await asyncio.to_thread(request.app.state.container.store.healthcheck)
        ok = bool(result.get("ok"))
        return JSONResponse(status_code=200 if ok else 503, content={"ok": ok})

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request):
        metrics_token = str(os.getenv("PORTHOUSE_METRICS_TOKEN") or "").strip()
        if not metrics_token:
            # Fail closed: without an explicit scrape token the endpoint is
            # disabled rather than exposed on the data plane.
            return Response(status_code=404, content="metrics endpoint disabled\n")
        if not hmac.compare_digest(
            _bearer_token(request.headers.get("authorization")), metrics_token
        ):
            return Response(status_code=401, content="metrics authentication required\n")
        cache = request.app.state.metrics_cache
        async with cache["lock"]:
            now = time.monotonic()
            if cache["data"] is not None and now < cache["expires_at"]:
                data = cache["data"]
            else:
                try:
                    data = await asyncio.to_thread(
                        request.app.state.container.store.operational_metrics
                    )
                except Exception:
                    # Keep the scrape endpoint observable during a database outage. The
                    # process is alive, but the data plane is not ready.
                    logger.exception("failed to collect operational metrics")
                    return Response(
                        content="# HELP porthouse_up API process readiness.\n"
                        "# TYPE porthouse_up gauge\nporthouse_up 0\n",
                        status_code=503,
                        media_type="text/plain; version=0.0.4",
                    )
                cache["data"] = data
                cache["expires_at"] = now + 5.0
        return Response(content=render_prometheus(data), media_type="text/plain; version=0.0.4")

    prefix = "/v1"
    app.include_router(system.router, prefix=prefix)
    if surface in {"combined", "control"}:
        app.include_router(auth.router, prefix=prefix)
        app.include_router(admin_platform.router, prefix=prefix)
        app.include_router(admin_apps.router, prefix=prefix)
        app.include_router(admin_teams.router, prefix=prefix)
        app.include_router(admin_catalog.router, prefix=prefix)
        app.include_router(admin_evals.router, prefix=prefix)
        app.include_router(admin_model_providers.router, prefix=prefix)
        app.include_router(admin_embedding_profiles.router, prefix=prefix)
        app.include_router(admin_experiments.router, prefix=prefix)
        app.include_router(admin_plugins.router, prefix=prefix)
        app.include_router(admin_prompts.router, prefix=prefix)
        app.include_router(admin_remote_connections.router, prefix=prefix)
        app.include_router(admin_scenarios.router, prefix=prefix)
        app.include_router(admin_skills.router, prefix=prefix)
    if surface in {"combined", "public"}:
        app.include_router(app_auth.router, prefix=prefix)
        app.include_router(graph_events.router, prefix=prefix)
        app.include_router(apps.router, prefix=prefix)
        app.include_router(event_triggers.router, prefix=prefix)
        app.include_router(action_items.router, prefix=prefix)
        app.include_router(runs.router, prefix=prefix)
        app.include_router(artifact_uploads.router, prefix=prefix)
        app.include_router(device_hosts.router, prefix=prefix)
        app.include_router(host_tools.router, prefix=prefix)
        app.include_router(model_grants.router, prefix=prefix)
        app.include_router(input_assets.router, prefix=prefix)
        app.include_router(knowledge.router, prefix=prefix)
        app.include_router(memory.router, prefix=prefix)
        app.include_router(sessions.router, prefix=prefix)
        app.include_router(schedules.router, prefix=prefix)
        app.include_router(works.router, prefix=prefix)
        app.include_router(workflows.router, prefix=prefix)
        app.mount("/mcp", mcp_gateway.asgi_app, name="mcp")

    ui_dir = Path(__file__).resolve().parent.parent / "static" / "ui"
    if surface in {"combined", "control"} and ui_dir.exists():
        app.mount("/ui", SPAStaticFiles(directory=str(ui_dir), html=True), name="ui")
    configure_telemetry(service_name=f"porthouse-api-{surface}", app=app)
    return app


_surface = os.getenv("PORTHOUSE_API_SURFACE", "combined").strip().lower()
if _surface not in {"combined", "public", "control"}:
    raise ValueError("PORTHOUSE_API_SURFACE must be combined, public, or control")
app = create_app(surface=_surface)
