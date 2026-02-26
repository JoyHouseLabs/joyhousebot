"""FastAPI server for joyhousebot client integration.

在整体架构中：Gateway 单端口提供 HTTP/WebSocket API，通过 app_state 注入 agent、bus、config 等，
与 gateway 命令共享同一进程。
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from joyhousebot.config.access import get_config as get_cached_config
from joyhousebot.config.loader import save_config, get_config_path
from joyhousebot.agent.loop import AgentLoop
from joyhousebot.api.rpc.browser_methods import try_handle_browser_method
from joyhousebot.api.rpc.chat_methods import try_handle_chat_runtime_method
from joyhousebot.api.rpc.control_state import try_handle_control_state_method
from joyhousebot.api.rpc.connect_methods import try_handle_connect_method
from joyhousebot.api.rpc.cron_methods import (
    build_cron_add_body_from_params,
    build_cron_patch_body_from_params,
    try_handle_cron_method,
)
from joyhousebot.api.rpc.dispatch_pipeline import run_handler_pipeline
from joyhousebot.api.rpc.exec_approval_methods import try_handle_exec_approval_method
from joyhousebot.api.rpc.sandbox_methods import try_handle_sandbox_method
from joyhousebot.api.rpc.error_boundary import http_exception_result, unhandled_exception_result, unknown_method_result
from joyhousebot.api.rpc.health_status_methods import try_handle_health_status_method
from joyhousebot.api.rpc.misc_methods import try_handle_misc_method
from joyhousebot.api.rpc.node_runtime_methods import try_handle_node_runtime_method
from joyhousebot.api.rpc.pairing_methods import try_handle_pairing_method
from joyhousebot.api.rpc.context_models import RpcDispatchContext, RpcDispatchHandlers
from joyhousebot.api.rpc.pipeline_builder import build_rpc_dispatch_handlers_from_context
from joyhousebot.api.rpc.pipeline_handlers import (
    handle_agents_with_shadow,
    handle_config_with_shadow,
    handle_sessions_usage_with_shadow,
)
from joyhousebot.api.rpc.lanes_methods import try_handle_lanes_method
from joyhousebot.api.rpc.traces_methods import try_handle_traces_method
from joyhousebot.api.rpc.plugin_gateway_methods import try_handle_plugin_gateway_method
from joyhousebot.api.rpc.plugins import try_handle_plugins_method
from joyhousebot.api.rpc.request_context import (
    make_broadcast_rpc_event_adapter,
    make_connect_logger,
    make_rpc_error_adapter,
    resolve_browser_control_url,
)
from joyhousebot.browser import create_browser_app
from joyhousebot.api.rpc.request_guard import prepare_rpc_request_context
from joyhousebot.api.rpc.web_login import try_handle_web_login_method
from joyhousebot.api.rpc.ws_rpc_methods import handle_rpc_connect_postprocess, run_rpc_ws_loop
from joyhousebot.api.rpc.ws_chat_methods import (
    run_chat_ws_loop,
)
from joyhousebot.api.rpc.ws_bootstrap import bootstrap_chat_ws_connection, bootstrap_rpc_ws_connection
from joyhousebot.gateway.auth_rate_limit import AuthRateLimiter
from joyhousebot.api.rpc.ws_error_handlers import handle_chat_ws_close, handle_rpc_ws_close
from joyhousebot.approvals import handle_exec_approval_requested, handle_exec_approval_resolved
from joyhousebot.api.http.task_methods import (
    get_task_response,
    list_task_events_response,
    list_tasks_response,
)
from joyhousebot.api.http.identity_methods import get_identity_response
from joyhousebot.api.http.cloud_connect_methods import (
    get_house_identity_response,
    register_house_response,
    bind_house_response,
    get_cloud_connect_status_response,
    start_cloud_connect_response,
    stop_cloud_connect_response,
)
from joyhousebot.api.http.cron_rest_methods import (
    add_cron_job_response,
    cron_job_to_dict,
    delete_cron_job_response,
    list_cron_jobs_response,
    patch_cron_job_response,
    run_cron_job_response,
    schedule_body_to_internal,
)
from joyhousebot.api.http.chat_methods import build_chat_response
from joyhousebot.api.http.config_methods import get_config_response, update_config_response
from joyhousebot.api.http.control_methods import (
    control_channels_response,
    control_overview_response,
    control_queue_response,
)
from joyhousebot.api.http.error_helpers import unknown_error_detail
from joyhousebot.api.http.message_methods import prepare_direct_message_send, publish_direct_message
from joyhousebot.api.http.openai_methods import (
    build_openai_non_streaming_response,
    build_openai_prompt,
    build_openai_streaming_response,
)
from joyhousebot.api.http.agent_methods import (
    get_agent_response,
    list_agents_response,
    patch_agent_response,
    resolve_agent_or_503,
)
from joyhousebot.api.http.session_methods import (
    delete_session_response,
    get_session_history_response,
    list_sessions_response,
)
from joyhousebot.api.http.skills_methods import list_skills_response, patch_skill_response
from joyhousebot.api.http.sandbox_methods import (
    list_sandbox_containers_response,
    sandbox_explain_response,
    sandbox_recreate_response,
)
from joyhousebot.api.http.transcription_methods import transcribe_upload_file
from joyhousebot.bus.queue import MessageBus
from joyhousebot.providers.litellm_provider import LiteLLMProvider
from joyhousebot.storage import LocalStateStore
from joyhousebot.providers.transcription import GroqTranscriptionProvider
from joyhousebot.agent.auth_profiles import build_auth_profile_alerts, build_auth_profiles_report
from joyhousebot.presence.store import PresenceStore
from joyhousebot.node import NodeInvokeResult, NodeRegistry, NodeSession
from joyhousebot.services.control.overview_service import build_channels_status_snapshot as service_build_channels_status_snapshot
from joyhousebot.services.skills.skill_service import build_skills_status_report as build_skills_status_report_from_service
from loguru import logger

# Presence: in-memory list of connected clients + gateway (OpenClaw-style)
presence_store = PresenceStore()


class ChatMessage(BaseModel):
    message: str
    session_id: str = "client:default"
    agent_id: str | None = None  # Multi-agent: which agent handles this; omit = default


class DirectMessageBody(BaseModel):
    channel: str
    target: str
    message: str
    reply_to: str | None = None
    metadata: dict[str, Any] | None = None


class ConfigUpdate(BaseModel):
    providers: dict[str, Any] | None = None
    agents: dict[str, Any] | None = None
    channels: dict[str, Any] | None = None
    tools: dict[str, Any] | None = None
    gateway: dict[str, Any] | None = None
    apps: dict[str, Any] | None = None  # e.g. {"enabled": ["app_id", ...]}
    skills: dict[str, Any] | None = None
    plugins: dict[str, Any] | None = None
    wallet: dict[str, Any] | None = None  # enabled, password (only when enabling)
    auth: dict[str, Any] | None = None
    approvals: dict[str, Any] | None = None
    browser: dict[str, Any] | None = None
    messages: dict[str, Any] | None = None
    commands: dict[str, Any] | None = None
    env: dict[str, Any] | None = None


class SkillPatchBody(BaseModel):
    enabled: bool


class AgentPatchBody(BaseModel):
    activated: bool | None = None  # Whether to show in chat page agent radio


class ActionValidateBody(BaseModel):
    code: str
    action: dict[str, Any] | None = None


class ActionValidateBatchBody(BaseModel):
    items: list[ActionValidateBody]


class TaskRequest(BaseModel):
    task_type: str
    payload: dict[str, Any]
    priority: int = 100


# OpenAI-compatible Chat Completions request (subset of fields)
class OpenAIChatMessage(BaseModel):
    role: str  # "system" | "user" | "assistant"
    content: str | list[Any] = ""  # string or array of content parts (OpenAI multipart)

    class Config:
        extra = "ignore"


class OpenAIChatCompletionsRequest(BaseModel):
    model: str = "joyhousebot"
    messages: list[OpenAIChatMessage]
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    session_id: str | None = None  # multi-session: use this session key (default openai:default)
    agent_id: str | None = None  # multi-agent: which agent to use
    class Config:
        extra = "ignore"


app_state = {
    "agent_loop": None,
    "message_bus": None,
    "provider": None,
    "config": None,
    "transcription_provider": None,
    "rpc_exec_approvals": {"version": 1, "defaults": {}, "agents": {}},
    "rpc_node_exec_approvals": {},
    "rpc_device_pairs": {"pending": [], "paired": []},
    "rpc_cron_runs": [],
    "rpc_last_heartbeat": None,
    "rpc_update_status": {"running": False, "startedAtMs": None, "finishedAtMs": None, "ok": None, "message": ""},
    "node_registry": None,
    "rpc_connections": {},
    "rpc_exec_approval_pending": {},
    "rpc_exec_approval_futures": {},
    "rpc_node_subscriptions": {},
    "rpc_agent_jobs": {},
    "rpc_agent_job_futures": {},
    "rpc_session_to_run_id": {},
    "rpc_lane_pending": {},  # session_key -> list of {runId, sessionKey, enqueuedAt, params}
    "rpc_abort_requested": set(),  # run_ids for which chat.abort was requested
    "plugin_manager": None,
    "plugin_snapshot": None,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan. When running inside gateway, state is pre-injected and we skip create/close."""
    if app_state.get("_gateway_injected"):
        if app_state.get("node_registry") is None:
            app_state["node_registry"] = NodeRegistry()
        logger.info("Joyhousebot API (gateway mode): using injected bus/agent")
        try:
            yield
        finally:
            pass  # gateway owns agent lifecycle
        return

    logger.info("Starting joyhousebot API server")
    try:
        config = get_cached_config(force_reload=True)
        app_state["config"] = config
        try:
            from joyhousebot.plugins.manager import initialize_plugins_for_workspace, get_plugin_manager

            openclaw_dir = (getattr(config.plugins, "openclaw_dir", None) or "").strip() or None
            app_state["plugin_snapshot"] = initialize_plugins_for_workspace(
                workspace=config.workspace_path,
                config=config,
                force_reload=True,
            )
            app_state["plugin_manager"] = get_plugin_manager(openclaw_dir=openclaw_dir)
            if app_state["plugin_snapshot"] is not None:
                app_state["plugin_manager"].start_services()
        except Exception as plugin_exc:
            logger.warning("Plugin host init skipped: {}", plugin_exc)

        # Local browser control service (OpenClaw-compatible); prefer its URL when enabled.
        if getattr(config, "browser", None) and getattr(config.browser, "enabled", False):
            try:
                browser_app = create_browser_app(
                    executable_path=getattr(config.browser, "executable_path", "") or "",
                    headless=getattr(config.browser, "headless", False),
                    default_profile=getattr(config.browser, "default_profile", "default") or "default",
                )
                app.mount("/__browser__", browser_app)
                app_state["browser_control_url"] = (
                    f"http://127.0.0.1:{getattr(config.gateway, 'port', 18790)}/__browser__"
                )
                logger.info("Browser control service mounted at /__browser__")
            except Exception as browser_exc:
                logger.warning("Browser control service init skipped: {}", browser_exc)

        default_model, default_fallbacks = config.get_agent_model_and_fallbacks(None)

        bus = MessageBus()
        provider = LiteLLMProvider(
            api_key=config.get_provider().api_key if config.get_provider() else None,
            api_base=config.get_api_base(),
            default_model=default_model,
            extra_headers=config.get_provider().extra_headers if config.get_provider() else None,
            provider_name=config.get_provider_name(),
        )

        app_state["node_registry"] = app_state.get("node_registry") or NodeRegistry()
        browser_runner = _make_browser_request_runner(app_state)
        node_invoke_runner = _make_node_invoke_runner(app_state)

        def _agent_rpc_err(code: str, msg: str, data: Any = None) -> dict[str, Any]:
            return {"ok": False, "error": {"code": code, "message": msg, "data": data}}

        async def _agent_request_exec_approval(
            command: str,
            timeout_ms: int = 120_000,
            request_id: str | None = None,
            session_key: str | None = None,
        ) -> str | None:
            """In-process exec.approval.request for code_runner when require_approval is True."""
            async def _noop_broadcast(_event: str, _payload: Any, _scopes: set[str] | None = None) -> None:
                pass

            result = await try_handle_exec_approval_method(
                method="exec.approval.request",
                params={
                    "id": request_id,
                    "command": command,
                    "timeoutMs": timeout_ms,
                    "twoPhase": False,
                    "sessionKey": session_key or "",
                },
                app_state=app_state,
                client_id="agent",
                rpc_error=_agent_rpc_err,
                cleanup_expired_exec_approvals=_cleanup_expired_exec_approvals,
                now_ms=_now_ms,
                broadcast_rpc_event=_noop_broadcast,
                load_persistent_state=_load_persistent_state,
                save_persistent_state=_save_persistent_state,
            )
            if result is None:
                return None
            ok, payload, _err = result
            if not ok or not isinstance(payload, dict):
                return None
            return payload.get("decision")

        async def _agent_approval_resolve(request_id: str, decision: str) -> tuple[bool, str]:
            """Resolve an exec approval from chat /approve command. Returns (ok, message)."""
            async def _noop_broadcast_resolve(_event: str, _payload: Any, _scopes: set[str] | None = None) -> None:
                pass

            result = await try_handle_exec_approval_method(
                method="exec.approval.resolve",
                params={"id": request_id, "requestId": request_id, "decision": decision},
                app_state=app_state,
                client_id="chat",
                rpc_error=_agent_rpc_err,
                cleanup_expired_exec_approvals=_cleanup_expired_exec_approvals,
                now_ms=_now_ms,
                broadcast_rpc_event=_noop_broadcast_resolve,
                load_persistent_state=_load_persistent_state,
                save_persistent_state=_save_persistent_state,
            )
            if result is None:
                return False, "Approval resolve failed (internal error)."
            ok, payload, err = result
            if not ok:
                err_msg = (err or {}).get("error", {}).get("message", "unknown") if isinstance(err, dict) else str(err)
                return False, f"Failed: {err_msg}"
            return True, "Approval resolved."

        transcription_provider = None
        if config.providers.groq.api_key:
            transcription_provider = GroqTranscriptionProvider(api_key=config.providers.groq.api_key)
        app_state["transcription_provider"] = transcription_provider

        agent = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=default_model,
            model_fallbacks=default_fallbacks,
            temperature=config.agents.defaults.temperature,
            max_tokens=config.agents.defaults.max_tokens,
            max_iterations=config.agents.defaults.max_tool_iterations,
            memory_window=config.agents.defaults.memory_window,
            max_context_tokens=getattr(config.agents.defaults, "max_context_tokens", None),
            brave_api_key=config.tools.web.search.api_key or None,
            exec_config=config.tools.exec,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            mcp_servers=config.tools.mcp_servers,
            config=config,
            browser_request_runner=browser_runner,
            node_invoke_runner=node_invoke_runner,
            exec_approval_request=_agent_request_exec_approval,
            approval_resolve_fn=_agent_approval_resolve,
            transcribe_provider=transcription_provider,
        )

        app_state["message_bus"] = bus
        app_state["provider"] = provider
        app_state["agent_loop"] = agent
        app_state["agents_map"] = {"default": agent}
        app_state["default_agent_id"] = "default"
        app_state["_start_time"] = time.time()

        async def _forward_exec_approval_requested(payload: dict) -> None:
            await handle_exec_approval_requested(app_state, payload)

        async def _forward_exec_approval_resolved(payload: dict) -> None:
            await handle_exec_approval_resolved(app_state, payload)

        app_state["on_exec_approval_requested"] = _forward_exec_approval_requested
        app_state["on_exec_approval_resolved"] = _forward_exec_approval_resolved

        # 可选：启动时用环境变量解密默认钱包，私钥驻留内存供签名等使用
        wallet_password = os.environ.get("JOYHOUSEBOT_WALLET_PASSWORD", "").strip()
        if wallet_password:
            try:
                from joyhousebot.identity.wallet_store import decrypt_wallet, get_wallet_address
                from joyhousebot.identity.unlocked_wallet import set_unlocked_private_key
                if get_wallet_address():
                    pk = decrypt_wallet(wallet_password)
                    set_unlocked_private_key(pk)
                    logger.info("Default wallet unlocked (private key in memory)")
            except Exception as e:
                logger.warning("Wallet unlock at startup failed: {}", e)

        logger.info("Joyhousebot API server initialized successfully")
        yield

    except Exception as e:
        logger.error(f"Failed to initialize API server: {e}")
        raise
    finally:
        try:
            from joyhousebot.identity.unlocked_wallet import set_unlocked_private_key
            set_unlocked_private_key(None)
        except Exception:
            pass
        if app_state.get("agent_loop") and not app_state.get("_gateway_injected"):
            await app_state["agent_loop"].close_mcp()
        plugin_manager = app_state.get("plugin_manager")
        if plugin_manager is not None and not app_state.get("_gateway_injected"):
            try:
                plugin_manager.stop_services()
            except Exception:
                pass
            try:
                plugin_manager.close()
            except Exception:
                pass
        logger.info("Joyhousebot API server stopped")


app = FastAPI(
    title="Joyhousebot API",
    description="API for joyhousebot client integration",
    version="1.0.0",
    lifespan=lifespan,
)

from joyhousebot.utils.exceptions import JoyhouseBotError, sanitize_error_message
from joyhousebot.api.rpc.error_boundary import classify_http_status


@app.exception_handler(JoyhouseBotError)
async def joyhousebot_exception_handler(request: Request, exc: JoyhouseBotError):
    from fastapi.responses import JSONResponse
    status_code = classify_http_status(exc)
    return JSONResponse(
        status_code=status_code,
        content=exc.to_dict()
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    from fastapi.responses import JSONResponse
    from joyhousebot.utils.exceptions import classify_exception
    code, category, _ = classify_exception(exc)
    sanitized = sanitize_error_message(str(exc))
    logger.exception(f"Unhandled exception [{code}]: {sanitized}")
    return JSONResponse(
        status_code=500,
        content={"error": "INTERNAL_ERROR", "message": "An unexpected error occurred", "code": code}
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount built config UI at /ui (built from frontend/; see README and scripts/build-ui.sh)
_ui_dir = Path(__file__).resolve().parent.parent / "static" / "ui"
if _ui_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(_ui_dir), html=True), name="ui")

_canvas_root_env = (os.getenv("JOYHOUSE_CANVAS_ROOT") or os.getenv("OPENCLAW_CANVAS_ROOT") or "").strip()
_canvas_dir = Path(_canvas_root_env) if _canvas_root_env else (Path.home() / ".joyhousebot" / "workspace" / "canvas")
try:
    _canvas_dir.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
app.mount(
    "/__openclaw__/canvas",
    StaticFiles(directory=str(_canvas_dir), html=True, check_dir=False),
    name="canvas-host",
)

# A2UI static assets (OpenClaw-compatible). Mount only if dir exists with index.html or a2ui.bundle.js.
_a2ui_root_env = (os.getenv("JOYHOUSE_A2UI_ROOT") or "").strip()
_a2ui_dir = Path(_a2ui_root_env) if _a2ui_root_env else (Path(__file__).resolve().parent.parent / "static" / "a2ui")
if _a2ui_dir.exists() and ((_a2ui_dir / "index.html").exists() or (_a2ui_dir / "a2ui.bundle.js").exists()):
    app.mount(
        "/__openclaw__/a2ui",
        StaticFiles(directory=str(_a2ui_dir), html=True, check_dir=False),
        name="a2ui-host",
    )


def _extract_http_api_credential(request: Request) -> str | None:
    """Extract credential from Authorization: Bearer, X-API-Key, or X-Control-Password."""
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        return auth[7:].strip()
    key = request.headers.get("X-API-Key", "").strip()
    if key:
        return key
    return request.headers.get("X-Control-Password", "").strip() or None


async def verify_http_api_token(request: Request) -> None:
    """Dependency: when gateway.control_token or control_password is set, require matching credential for /api."""
    config = get_cached_config()
    token = (getattr(config.gateway, "control_token", None) or "").strip()
    password = (getattr(config.gateway, "control_password", None) or "").strip()
    if not token and not password:
        return
    provided = _extract_http_api_credential(request)
    if not provided:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
    if token and hmac.compare_digest(token, provided):
        return
    if password and hmac.compare_digest(password, provided):
        return
    raise HTTPException(status_code=401, detail="Invalid or missing API token")


# API router: all JSON API routes under /api
api_router = APIRouter(dependencies=[Depends(verify_http_api_token)])

# Plugin apps (webapp dist) list API; static files stay at /plugins-apps/
@api_router.get("/plugins/apps")
async def list_plugins_apps():
    """List plugin webapps for frontend AppHost (app_id, name, route, entry, base_url, app_link, icon_url)."""
    from joyhousebot.plugins.apps import resolve_plugin_apps
    config = get_cached_config()
    workspace = Path(config.workspace_path).expanduser().resolve()
    apps = resolve_plugin_apps(workspace, config)
    for a in apps:
        app_id = a["app_id"]
        a["base_url"] = f"/plugins-apps/{app_id}"
        a["app_link"] = f"/plugins-apps/{app_id}/index.html"
        icon_path = a.get("icon")
        if icon_path and isinstance(icon_path, str):
            # Sanitize: no parent path segments
            parts = icon_path.replace("\\", "/").strip().split("/")
            if not any(p in ("", ".", "..") for p in parts):
                a["icon_url"] = f"/plugins-apps/{app_id}/{'/'.join(parts)}"
    return {"ok": True, "apps": apps}


@app.get("/plugins-apps/{app_id}/{path:path}")
async def serve_plugin_app_file(app_id: str, path: str):
    """Serve a file from a plugin webapp dist directory."""
    from joyhousebot.plugins.apps import resolve_plugin_apps
    config = get_cached_config()
    workspace = Path(config.workspace_path).expanduser().resolve()
    apps = resolve_plugin_apps(workspace, config)
    app = next((a for a in apps if a.get("app_id") == app_id), None)
    if not app or not app.get("base_path"):
        raise HTTPException(status_code=404, detail="App not found or no base_path")
    base = Path(app["base_path"])
    if not base.is_dir():
        raise HTTPException(status_code=404, detail="App dist not found")
    # Prevent path traversal
    resolved = (base / path).resolve()
    if not str(resolved).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Invalid path")
    if resolved.is_file():
        return FileResponse(str(resolved))
    if resolved.is_dir() and (resolved / "index.html").exists():
        return FileResponse(str(resolved / "index.html"))
    raise HTTPException(status_code=404, detail="Not found")


@app.get("/plugins-apps/{app_id}")
async def serve_plugin_app_index(app_id: str):
    """Serve plugin app index (redirect to index.html)."""
    from joyhousebot.plugins.apps import resolve_plugin_apps
    config = get_cached_config()
    workspace = Path(config.workspace_path).expanduser().resolve()
    apps = resolve_plugin_apps(workspace, config)
    app = next((a for a in apps if a.get("app_id") == app_id), None)
    if not app or not app.get("base_path"):
        raise HTTPException(status_code=404, detail="App not found")
    base = Path(app["base_path"])
    index = base / "index.html"
    if index.exists():
        return FileResponse(str(index))
    raise HTTPException(status_code=404, detail="index.html not found")


def _resolve_agent(agent_id: str | None = None):
    """Resolve AgentLoop by agent_id (multi-agent); fallback to default."""
    agents_map = app_state.get("agents_map") or {}
    default_id = app_state.get("default_agent_id") or "default"
    aid = (agent_id or default_id).strip() if agent_id else default_id
    if agents_map and aid in agents_map:
        return agents_map[aid]
    return app_state.get("agent_loop")


class ConnectionManager:
    """Manage WebSocket connections."""
    
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
    
    async def broadcast(self, message: dict[str, Any]):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()
# Map WebSocket -> presence connection_key for disconnect cleanup
ws_to_presence_key: dict[WebSocket, str] = {}

# OpenClaw-compatible RPC surface (subset + joyhousebot extensions)
GATEWAY_METHODS = [
    "connect",
    "health",
    "status",
    "agents.list",
    "agents.create",
    "agents.update",
    "agents.delete",
    "agents.files.list",
    "agents.files.get",
    "agents.files.set",
    "models.list",
    "auth.profiles.status",
    "actions.catalog",
    "actions.validate",
    "actions.validate.batch",
    "actions.validate.batch.lifecycle",
    "alerts.lifecycle",
    "chat.send",
    "chat.inject",
    "chat.abort",
    "chat.history",
    "agent",
    "agent.wait",
    "lanes.status",
    "lanes.list",
    "traces.list",
    "traces.get",
    "sessions.list",
    "sessions.resolve",
    "sessions.preview",
    "sessions.patch",
    "sessions.reset",
    "sessions.delete",
    "sessions.compact",
    "config.get",
    "config.schema",
    "config.patch",
    "config.set",
    "config.apply",
    "skills.status",
    "skills.update",
    "skills.install",
    "plugins.list",
    "plugins.info",
    "plugins.doctor",
    "plugins.reload",
    "plugins.gateway.methods",
    "plugins.http.dispatch",
    "plugins.cli.list",
    "plugins.cli.invoke",
    "plugins.channels.list",
    "plugins.providers.list",
    "plugins.hooks.list",
    "talk.config",
    "voicewake.get",
    "voicewake.set",
    "wizard.start",
    "wizard.next",
    "tts.status",
    "tts.enable",
    "tts.disable",
    "tts.convert",
    "tts.providers",
    "channels.status",
    "channels.logout",
    "system-presence",
    "logs.tail",
    "last-heartbeat",
    "sessions.usage",
    "sessions.usage.timeseries",
    "sessions.usage.logs",
    "usage.cost",
    "usage.status",
    "update.run",
    "doctor.memory.status",
    "push.test",
    "agent.identity.get",
    "device.pair.list",
    "device.pair.approve",
    "device.pair.reject",
    "device.pair.remove",
    "device.token.rotate",
    "device.token.revoke",
    "node.list",
    "node.describe",
    "node.rename",
    "node.pair.request",
    "node.pair.list",
    "node.pair.approve",
    "node.pair.reject",
    "node.pair.verify",
    "node.invoke",
    "node.invoke.result",
    "node.event",
    "browser.request",
    "exec.approval.request",
    "exec.approval.waitDecision",
    "exec.approval.resolve",
    "exec.approvals.get",
    "exec.approvals.pending",
    "exec.approvals.set",
    "exec.approvals.node.get",
    "exec.approvals.node.set",
    "web.login.start",
    "web.login.wait",
    "cron.list",
    "cron.status",
    "cron.add",
    "cron.update",
    "cron.remove",
    "cron.run",
    "cron.runs",
]


def _plugin_gateway_methods() -> list[str]:
    snapshot = app_state.get("plugin_snapshot")
    if snapshot is not None and hasattr(snapshot, "gateway_methods"):
        try:
            return [str(x) for x in getattr(snapshot, "gateway_methods", [])]
        except Exception:
            return []
    manager = app_state.get("plugin_manager")
    if manager is None:
        return []
    try:
        return [str(x) for x in manager.gateway_methods()]
    except Exception:
        return []


def _gateway_methods_with_plugins() -> list[str]:
    return list(dict.fromkeys([*GATEWAY_METHODS, *_plugin_gateway_methods()]))

GATEWAY_EVENTS = [
    "connect.challenge",
    "agent",
    "chat",
    "presence",
    "tick",
    "health",
    "cron",
    "lanes.enqueued",
    "lanes.dequeued",
    "lanes.completed",
    "lanes.depth.changed",
    "device.pair.requested",
    "device.pair.resolved",
    "exec.approval.requested",
    "exec.approval.resolved",
    "node.pair.requested",
    "node.pair.resolved",
    "node.event",
]

READ_METHODS = {
    "health",
    "status",
    "agents.list",
    "agents.files.list",
    "agents.files.get",
    "models.list",
    "auth.profiles.status",
    "actions.catalog",
    "actions.validate",
    "actions.validate.batch",
    "actions.validate.batch.lifecycle",
    "alerts.lifecycle",
    "agent.wait",
    "lanes.status",
    "lanes.list",
    "traces.list",
    "traces.get",
    "chat.history",
    "last-heartbeat",
    "sessions.list",
    "sessions.resolve",
    "sessions.preview",
    "config.get",
    "config.schema",
    "skills.status",
    "plugins.list",
    "plugins.info",
    "plugins.doctor",
    "plugins.gateway.methods",
    "plugins.http.dispatch",
    "plugins.cli.list",
    "plugins.channels.list",
    "plugins.providers.list",
    "plugins.hooks.list",
    "talk.config",
    "voicewake.get",
    "tts.status",
    "tts.providers",
    "exec.approval.waitDecision",
    "channels.status",
    "system-presence",
    "logs.tail",
    "sessions.usage",
    "sessions.usage.timeseries",
    "sessions.usage.logs",
    "usage.cost",
    "usage.status",
    "agent.identity.get",
    "device.pair.list",
    "exec.approvals.get",
    "exec.approvals.node.get",
    "node.list",
    "node.describe",
    "node.pair.list",
    "web.login.wait",
    "cron.list",
    "cron.status",
    "cron.runs",
}

WRITE_METHODS = {
    "chat.send",
    "chat.inject",
    "chat.abort",
    "agent",
    "agents.create",
    "agents.update",
    "agents.delete",
    "agents.files.set",
    "sessions.patch",
    "sessions.reset",
    "sessions.delete",
    "sessions.compact",
    "config.patch",
    "config.set",
    "config.apply",
    "skills.update",
    "skills.install",
    "plugins.reload",
    "plugins.http.dispatch",
    "plugins.cli.invoke",
    "talk.config",
    "voicewake.set",
    "wizard.start",
    "wizard.next",
    "tts.enable",
    "tts.disable",
    "tts.convert",
    "channels.logout",
    "update.run",
    "device.pair.approve",
    "device.pair.reject",
    "device.token.rotate",
    "device.token.revoke",
    "node.invoke",
    "node.rename",
    "node.pair.request",
    "node.pair.approve",
    "node.pair.reject",
    "node.pair.verify",
    "node.invoke.result",
    "node.event",
    "browser.request",
    "exec.approval.request",
    "exec.approval.resolve",
    "exec.approvals.set",
    "exec.approvals.node.set",
    "web.login.start",
    "cron.add",
    "cron.update",
    "cron.remove",
    "cron.run",
}

NODE_ROLE_METHODS = {
    "node.pair.request",
    "node.pair.verify",
    "node.invoke.result",
    "node.event",
    "exec.approval.request",
    "exec.approval.waitDecision",
}

APPROVAL_METHODS = {
    "exec.approval.request",
    "exec.approval.waitDecision",
    "exec.approval.resolve",
    "exec.approvals.get",
    "exec.approvals.pending",
}

PAIRING_METHODS = {
    "node.pair.request",
    "node.pair.list",
    "node.pair.approve",
    "node.pair.reject",
    "node.pair.verify",
    "device.pair.list",
    "device.pair.approve",
    "device.pair.reject",
    "device.token.rotate",
    "device.token.revoke",
    "node.rename",
}

# Methods that require operator.admin (not just operator.write)
ADMIN_ONLY_METHODS = {
    "config.patch",
    "config.set",
    "config.apply",
    "agents.create",
    "agents.update",
    "agents.delete",
    "skills.update",
    "skills.install",
    "sessions.patch",
    "sessions.reset",
    "sessions.delete",
    "sessions.compact",
    "cron.add",
    "cron.update",
    "cron.remove",
    "cron.run",
    "channels.logout",
    "update.run",
    "wizard.start",
    "wizard.next",
    "exec.approvals.set",
    "exec.approvals.node.set",
}


@dataclass
class RpcClientState:
    connected: bool = False
    role: str = "operator"
    scopes: set[str] = field(default_factory=set)
    client_id: str | None = None


def _rpc_error(code: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    err = {"code": code, "message": message}
    if data:
        err["data"] = data
    return err


def _is_method_allowed_by_canary(method: str, config: Any) -> bool:
    gateway = getattr(config, "gateway", None)
    allowed = getattr(gateway, "rpc_canary_methods", []) if gateway else []
    if not allowed:
        return True
    if method in {"connect", "health", "status"}:
        return True
    return method in set(allowed)


def _authorize_rpc_method(method: str, client: RpcClientState, config: Any) -> dict[str, Any] | None:
    if method == "connect":
        return None
    if not client.connected:
        return _rpc_error("INVALID_REQUEST", "must call connect first")
    if client.role == "node":
        if method in NODE_ROLE_METHODS:
            return None
        return _rpc_error("INVALID_REQUEST", f"unauthorized method for node role: {method}")
    if client.role != "operator":
        return _rpc_error("INVALID_REQUEST", f"unauthorized role: {client.role}")
    # Admin scope grants all methods.
    if "operator.admin" in client.scopes:
        return None
    if method in APPROVAL_METHODS:
        if "operator.approvals" in client.scopes:
            return None
        return _rpc_error("INVALID_REQUEST", "missing scope: operator.approvals")
    if method in PAIRING_METHODS:
        if "operator.pairing" in client.scopes:
            return None
        return _rpc_error("INVALID_REQUEST", "missing scope: operator.pairing")
    if method in READ_METHODS:
        if "operator.read" in client.scopes or "operator.write" in client.scopes:
            return None
        return _rpc_error("INVALID_REQUEST", "missing scope: operator.read")
    if method in ADMIN_ONLY_METHODS:
        return _rpc_error("INVALID_REQUEST", "missing scope: operator.admin")
    if method in WRITE_METHODS:
        if "operator.write" in client.scopes:
            return None
        return _rpc_error("INVALID_REQUEST", "missing scope: operator.write")
    if method.startswith("exec.approvals."):
        return _rpc_error("INVALID_REQUEST", "missing scope: operator.admin")
    if method in set(_plugin_gateway_methods()):
        if "operator.write" in client.scopes:
            return None
        return _rpc_error("INVALID_REQUEST", "missing scope: operator.write")
    return _rpc_error("INVALID_REQUEST", "unknown or unauthorized method")


def _get_models_payload(config: Any) -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in config.get_agent_list_for_api():
        model = (entry.get("model") or "").strip()
        if not model or model in seen:
            continue
        seen.add(model)
        models.append({
            "id": model,
            "label": model,
            "provider": entry.get("provider_name"),
        })
    return models


def _apply_session_patch(session: Any, params: dict[str, Any]) -> dict[str, Any]:
    changed = False
    label = params.get("label")
    if label is not None:
        session.metadata["label"] = str(label) if label else ""
        changed = True
    thinking_level = params.get("thinkingLevel")
    if thinking_level is not None:
        session.metadata["thinkingLevel"] = str(thinking_level) if thinking_level else None
        changed = True
    verbose_level = params.get("verboseLevel")
    if verbose_level is not None:
        session.metadata["verboseLevel"] = str(verbose_level) if verbose_level else None
        changed = True
    reasoning_level = params.get("reasoningLevel")
    if reasoning_level is not None:
        session.metadata["reasoningLevel"] = str(reasoning_level) if reasoning_level else None
        changed = True
    labels = params.get("labels")
    if labels is not None and isinstance(labels, list):
        session.metadata["labels"] = [str(x) for x in labels]
        changed = True
    send_policy = params.get("sendPolicy")
    if send_policy is not None:
        session.metadata["sendPolicy"] = str(send_policy)
        changed = True
    model = params.get("model")
    if model is not None:
        session.metadata["model"] = str(model)
        changed = True
    custom = params.get("metadata")
    if isinstance(custom, dict):
        session.metadata.update(custom)
        changed = True
    return {"changed": changed, "metadata": session.metadata}


async def _run_rpc_shadow(method: str, params: dict[str, Any], payload: Any) -> None:
    """Best-effort read-only shadow comparison for canary rollout."""
    if method not in READ_METHODS:
        return
    config = app_state.get("config") or get_cached_config()
    if not getattr(config.gateway, "rpc_shadow_reads", False):
        return
    try:
        # Shadow path: reuse legacy HTTP endpoints and compare serialized payload.
        if method == "health":
            shadow = await health()
        elif method == "status":
            shadow = await control_overview()
        elif method == "agents.list":
            shadow = await list_agents()
        elif method == "config.get":
            shadow = await handle_get_config()
        elif method == "sessions.list":
            shadow = await list_sessions(agent_id=params.get("agent_id"))
        else:
            return
        if json.dumps(shadow, sort_keys=True, default=str) != json.dumps(payload, sort_keys=True, default=str):
            logger.warning("RPC shadow diff detected for method={}", method)
    except Exception as e:
        logger.warning("RPC shadow run failed for method={}: {}", method, e)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _resolve_canvas_host_url(config: Any) -> str | None:
    env_url = (os.getenv("JOYHOUSE_CANVAS_HOST_URL") or os.getenv("OPENCLAW_CANVAS_HOST_URL") or "").strip()
    if env_url:
        return env_url
    host = str(getattr(config.gateway, "host", "") or "").strip()
    port = int(getattr(config.gateway, "port", 0) or 0)
    if not host or not port:
        return None
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{port}"


# Event -> required scopes (client must have at least one, or operator.admin)
EVENT_SCOPE_GUARDS: dict[str, list[str]] = {
    "exec.approval.requested": ["operator.approvals"],
    "exec.approval.resolved": ["operator.approvals"],
    "device.pair.requested": ["operator.pairing"],
    "device.pair.resolved": ["operator.pairing"],
    "node.pair.requested": ["operator.pairing"],
    "node.pair.resolved": ["operator.pairing"],
}


def _connection_has_event_scope(entry: dict[str, Any], event: str) -> bool:
    required = EVENT_SCOPE_GUARDS.get(event)
    if not required:
        return True
    role = str(entry.get("role") or "")
    if role != "operator":
        return False
    scopes = entry.get("scopes")
    if isinstance(scopes, (list, set)):
        if "operator.admin" in scopes:
            return True
        return any(s in scopes for s in required)
    return False


async def _broadcast_rpc_event(event: str, payload: Any, *, roles: set[str] | None = None) -> None:
    connections = app_state.get("rpc_connections") or {}
    dead: list[str] = []
    for conn_id, entry in list(connections.items()):
        ws = entry.get("websocket") if isinstance(entry, dict) else None
        role = str(entry.get("role") or "") if isinstance(entry, dict) else ""
        if roles is not None and role not in roles:
            continue
        if not _connection_has_event_scope(entry if isinstance(entry, dict) else {}, event):
            continue
        if ws is None:
            dead.append(conn_id)
            continue
        try:
            await ws.send_json({"type": "event", "event": event, "payload": payload})
        except Exception:
            dead.append(conn_id)
    for conn_id in dead:
        connections.pop(conn_id, None)
    app_state["rpc_connections"] = connections


def _load_device_pairs_state() -> dict[str, Any]:
    state = _load_persistent_state("rpc.device_pairs", {"pending": [], "paired": []})
    if not isinstance(state, dict):
        return {"pending": [], "paired": []}
    pending = state.get("pending")
    paired = state.get("paired")
    return {
        "pending": pending if isinstance(pending, list) else [],
        "paired": paired if isinstance(paired, list) else [],
    }


def _normalize_platform_id(platform: str | None, device_family: str | None) -> str:
    raw = (platform or "").strip().lower()
    if raw.startswith("ios"):
        return "ios"
    if raw.startswith("android"):
        return "android"
    if raw.startswith("mac") or raw.startswith("darwin"):
        return "macos"
    if raw.startswith("win"):
        return "windows"
    if raw.startswith("linux"):
        return "linux"
    family = (device_family or "").strip().lower()
    if "iphone" in family or "ipad" in family or "ios" in family:
        return "ios"
    if "android" in family:
        return "android"
    if "mac" in family:
        return "macos"
    if "windows" in family:
        return "windows"
    if "linux" in family:
        return "linux"
    return "unknown"


def _default_allowlist_for_platform(platform_id: str) -> set[str]:
    canvas_cmds = {
        "canvas.present",
        "canvas.hide",
        "canvas.navigate",
        "canvas.eval",
        "canvas.snapshot",
        "canvas.a2ui.push",
        "canvas.a2ui.pushJSONL",
        "canvas.a2ui.reset",
    }
    common_cmds = {"device.info", "device.status", "location.get", "camera.list"}
    system_cmds = {"system.run", "system.which", "system.notify", "browser.proxy"}
    ios_cmds = canvas_cmds | common_cmds | {"system.notify", "browser.proxy"}
    android_cmds = canvas_cmds | common_cmds | {"browser.proxy"}
    macos_cmds = canvas_cmds | common_cmds | system_cmds
    linux_cmds = set(system_cmds)
    windows_cmds = set(system_cmds)
    unknown_cmds = canvas_cmds | {"camera.list", "location.get"} | system_cmds
    mapping = {
        "ios": ios_cmds,
        "android": android_cmds,
        "macos": macos_cmds,
        "linux": linux_cmds,
        "windows": windows_cmds,
        "unknown": unknown_cmds,
    }
    return set(mapping.get(platform_id, unknown_cmds))


def _resolve_node_command_allowlist(config: Any, node: Any) -> set[str]:
    platform_id = _normalize_platform_id(getattr(node, "platform", None), getattr(node, "device_family", None))
    allow = _default_allowlist_for_platform(platform_id)
    extra = getattr(config.gateway, "node_allow_commands", []) or []
    deny = set(getattr(config.gateway, "node_deny_commands", []) or [])
    for cmd in extra:
        c = str(cmd).strip()
        if c:
            allow.add(c)
    for cmd in deny:
        c = str(cmd).strip()
        if c:
            allow.discard(c)
    return allow


def _is_node_command_allowed(command: str, declared_commands: list[str], allowlist: set[str]) -> tuple[bool, str]:
    cmd = (command or "").strip()
    if not cmd:
        return False, "command required"
    if cmd not in allowlist:
        return False, "command not allowlisted"
    declared = [str(c).strip() for c in (declared_commands or []) if str(c).strip()]
    if not declared:
        return False, "node did not declare commands"
    if cmd not in declared:
        return False, "command not declared by node"
    return True, ""


def _resolve_browser_node(nodes: list[Any], target: str | None) -> Any | None:
    browser_nodes = [n for n in nodes if ("browser" in (n.caps or [])) or ("browser.proxy" in (n.commands or []))]
    if not browser_nodes:
        return None
    query = (target or "").strip()
    if not query:
        if len(browser_nodes) == 1:
            return browser_nodes[0]
        return None
    q = query.lower()
    matches = []
    for node in browser_nodes:
        nid = str(getattr(node, "node_id", "") or "")
        name = str(getattr(node, "display_name", "") or "")
        rip = str(getattr(node, "remote_ip", "") or "")
        if nid == query or rip == query or name.lower() == q or nid.startswith(query):
            matches.append(node)
    if len(matches) == 1:
        return matches[0]
    return None


def _browser_proxy_media_dir() -> Path:
    d = Path.home() / ".joyhousebot" / "media" / "browser"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _persist_browser_proxy_files(files: list[dict[str, Any]] | None) -> dict[str, str]:
    if not files:
        return {}
    mapping: dict[str, str] = {}
    root = _browser_proxy_media_dir()
    for entry in files:
        if not isinstance(entry, dict):
            continue
        src_path = str(entry.get("path") or "").strip()
        raw_b64 = str(entry.get("base64") or "").strip()
        if not src_path or not raw_b64:
            continue
        mime = str(entry.get("mimeType") or "").strip().lower()
        suffix = ".bin"
        if "png" in mime:
            suffix = ".png"
        elif "jpeg" in mime or "jpg" in mime:
            suffix = ".jpg"
        elif "webp" in mime:
            suffix = ".webp"
        elif "gif" in mime:
            suffix = ".gif"
        elif "json" in mime:
            suffix = ".json"
        elif "pdf" in mime:
            suffix = ".pdf"
        target = root / f"browser-proxy-{uuid.uuid4().hex[:12]}{suffix}"
        try:
            target.write_bytes(base64.b64decode(raw_b64))
            mapping[src_path] = str(target)
        except Exception:
            continue
    return mapping


def _apply_browser_proxy_paths(result: Any, mapping: dict[str, str]) -> None:
    if not isinstance(result, dict) or not mapping:
        return
    path_value = result.get("path")
    if isinstance(path_value, str) and path_value in mapping:
        result["path"] = mapping[path_value]
    image_path_value = result.get("imagePath")
    if isinstance(image_path_value, str) and image_path_value in mapping:
        result["imagePath"] = mapping[image_path_value]
    download = result.get("download")
    if isinstance(download, dict):
        dpath = download.get("path")
        if isinstance(dpath, str) and dpath in mapping:
            download["path"] = mapping[dpath]


def _make_browser_request_runner(app_state: dict) -> Callable[..., Awaitable[Any]]:
    """Build async runner that runs one browser.request using app_state (config, node_registry, browser_control_url)."""

    async def runner(
        *,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: Any = None,
        timeout_ms: int = 30000,
    ) -> Any:
        from joyhousebot.api.rpc.browser_methods import run_browser_request

        config = app_state.get("config")
        node_registry = app_state.get("node_registry")
        if not config or not node_registry:
            raise RuntimeError("browser runner: config or node_registry not available")
        browser_control_url = (
            (app_state.get("browser_control_url") or "").strip()
            or resolve_browser_control_url()
        )
        ok, result, err = await run_browser_request(
            config=config,
            node_registry=node_registry,
            resolve_browser_node=_resolve_browser_node,
            resolve_node_command_allowlist=_resolve_node_command_allowlist,
            is_node_command_allowed=_is_node_command_allowed,
            persist_browser_proxy_files=_persist_browser_proxy_files,
            apply_browser_proxy_paths=_apply_browser_proxy_paths,
            browser_control_url=browser_control_url,
            method_raw=method,
            path=path,
            query=query,
            body=body,
            timeout_ms=timeout_ms,
        )
        if not ok and err:
            raise RuntimeError(err.get("message", "browser request failed"))
        return result

    return runner


def _make_node_invoke_runner(app_state: dict) -> Callable[..., Awaitable[NodeInvokeResult]]:
    """Build async runner that performs node.invoke with optional node resolution by id or display name."""

    async def runner(
        *,
        node_id_or_name: str | None = None,
        command: str,
        params: dict[str, Any] | None = None,
        timeout_ms: int = 30000,
    ) -> NodeInvokeResult:
        node_registry: NodeRegistry = app_state.get("node_registry") or NodeRegistry()
        config = app_state.get("config")
        if not config:
            return NodeInvokeResult(ok=False, error={"code": "UNAVAILABLE", "message": "config not available"})
        connected = node_registry.list_connected()
        if not connected:
            return NodeInvokeResult(
                ok=False,
                error={"code": "NOT_CONNECTED", "message": "no nodes connected"},
            )
        node_id: str
        if not (node_id_or_name or "").strip():
            node_id = connected[0].node_id
        else:
            query = (node_id_or_name or "").strip()
            matched = None
            for session in connected:
                if session.node_id == query or (session.display_name or "").strip() == query:
                    matched = session
                    break
            if not matched:
                return NodeInvokeResult(
                    ok=False,
                    error={"code": "NOT_FOUND", "message": f"node not found: {query!r}"},
                )
            node_id = matched.node_id
        node_session = node_registry.get(node_id)
        if not node_session:
            return NodeInvokeResult(
                ok=False,
                error={"code": "NOT_CONNECTED", "message": f"node {node_id!r} not connected"},
            )
        allowlist = _resolve_node_command_allowlist(config, node_session)
        allowed, reason = _is_node_command_allowed(command, node_session.commands, allowlist)
        if not allowed:
            return NodeInvokeResult(
                ok=False,
                error={"code": "NOT_ALLOWED", "message": reason or "node command not allowed"},
            )
        return await node_registry.invoke(
            node_id=node_id,
            command=command,
            params=params,
            timeout_ms=max(100, timeout_ms),
        )

    return runner


def _cleanup_expired_exec_approvals(now_ms: int | None = None) -> None:
    now = now_ms or _now_ms()
    pending = app_state.get("rpc_exec_approval_pending") or {}
    futures = app_state.get("rpc_exec_approval_futures") or {}
    expired_ids = []
    for rid, rec in list(pending.items()):
        expires_at = int(rec.get("expiresAtMs") or 0) if isinstance(rec, dict) else 0
        if expires_at > 0 and expires_at <= now and not rec.get("decision"):
            rec["decision"] = None
            rec["status"] = "expired"
            fut = futures.get(rid)
            if fut is not None and not fut.done():
                fut.set_result(None)
            expired_ids.append(rid)
    # Keep expired records briefly; but remove dangling futures immediately.
    for rid in expired_ids:
        futures.pop(rid, None)
    app_state["rpc_exec_approval_pending"] = pending
    app_state["rpc_exec_approval_futures"] = futures


def _normalize_node_event_payload(params: dict[str, Any]) -> tuple[Any, str | None]:
    payload = params.get("payload")
    payload_json = params.get("payloadJSON")
    if payload is None and isinstance(payload_json, str):
        try:
            payload = json.loads(payload_json)
        except Exception:
            payload = None
    if payload_json is None and payload is not None:
        try:
            payload_json = json.dumps(payload, ensure_ascii=False)
        except Exception:
            payload_json = None
    return payload, payload_json if isinstance(payload_json, str) else None


def _normalize_agent_id(raw: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", raw.strip().lower()).strip("-")
    return value or "agent"


def _ensure_agent_workspace_bootstrap(workspace: Path, *, agent_name: str | None = None) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    agents_md = workspace / "AGENTS.md"
    if not agents_md.exists():
        agents_md.write_text("# AGENTS\n\nProject-level instructions for this agent workspace.\n", encoding="utf-8")
    memory_dir = workspace / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_md = memory_dir / "MEMORY.md"
    if not memory_md.exists():
        memory_md.write_text("# MEMORY\n\nLong-term memory notes.\n", encoding="utf-8")
    identity_md = workspace / "IDENTITY.md"
    if not identity_md.exists():
        title = (agent_name or "").strip() or "Agent"
        identity_md.write_text(f"- Name: {title}\n", encoding="utf-8")


def _register_agent_job(run_id: str, session_key: str | None = None) -> bool:
    jobs = app_state.get("rpc_agent_jobs") or {}
    futures = app_state.get("rpc_agent_job_futures") or {}
    session_to_run = app_state.get("rpc_session_to_run_id") or {}
    config = app_state.get("config")
    serialization = (
        config is not None
        and getattr(getattr(config, "gateway", None), "chat_session_serialization", True)
    )
    if serialization and session_key:
        if session_key in session_to_run:
            return False
    current = jobs.get(run_id)
    if isinstance(current, dict) and current.get("status") in {"running", "ok", "error"}:
        return False
    jobs[run_id] = {
        "runId": run_id,
        "status": "running",
        "startedAt": _now_ms(),
        "endedAt": None,
        "error": None,
        "sessionKey": session_key,
    }
    if session_key:
        session_to_run[session_key] = run_id
    fut = asyncio.get_running_loop().create_future()
    futures[run_id] = fut
    app_state["rpc_agent_jobs"] = jobs
    app_state["rpc_agent_job_futures"] = futures
    app_state["rpc_session_to_run_id"] = session_to_run
    return True


def _request_abort(run_id: str) -> None:
    """Mark run_id as requested for abort (used by chat.abort)."""
    s = app_state.get("rpc_abort_requested")
    if s is None:
        s = set()
        app_state["rpc_abort_requested"] = s
    s.add(run_id)


def _check_abort_requested(run_id: str) -> bool:
    """Return True if chat.abort was requested for this run_id."""
    s = app_state.get("rpc_abort_requested")
    return bool(s and run_id in s)


def _clear_abort_requested(run_id: str) -> None:
    """Remove run_id from abort set (called when run completes)."""
    s = app_state.get("rpc_abort_requested")
    if s:
        s.discard(run_id)


def _complete_agent_job(
    run_id: str, *, status: str, error: str | None = None, result: dict[str, Any] | None = None
) -> None:
    _clear_abort_requested(run_id)
    jobs = app_state.get("rpc_agent_jobs") or {}
    futures = app_state.get("rpc_agent_job_futures") or {}
    session_to_run = app_state.get("rpc_session_to_run_id") or {}
    rec = jobs.get(run_id) or {"runId": run_id, "startedAt": _now_ms()}
    rec["status"] = status
    rec["endedAt"] = _now_ms()
    rec["error"] = error
    if result:
        rec.update(result)
    jobs[run_id] = rec
    sk = rec.get("sessionKey")
    if sk and sk in session_to_run and session_to_run.get(sk) == run_id:
        del session_to_run[sk]
    fut = futures.get(run_id)
    if fut is not None and not fut.done():
        fut.set_result(rec)
    app_state["rpc_agent_jobs"] = jobs
    app_state["rpc_agent_job_futures"] = futures
    app_state["rpc_session_to_run_id"] = session_to_run


def _get_running_run_id_for_session(session_key: str) -> str | None:
    session_to_run = app_state.get("rpc_session_to_run_id") or {}
    return session_to_run.get(session_key)


async def _wait_agent_job(run_id: str, timeout_ms: int) -> dict[str, Any] | None:
    jobs = app_state.get("rpc_agent_jobs") or {}
    futures = app_state.get("rpc_agent_job_futures") or {}
    current = jobs.get(run_id)
    if isinstance(current, dict) and current.get("status") in {"ok", "error"}:
        return current
    fut = futures.get(run_id)
    if fut is None:
        return None
    try:
        return await asyncio.wait_for(fut, timeout=max(0, timeout_ms) / 1000.0)
    except asyncio.TimeoutError:
        return None


def _hash_pairing_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _fanout_chat_to_subscribed_nodes(*, session_key: str, payload: dict[str, Any]) -> None:
    subscriptions: dict[str, set[str]] = app_state.get("rpc_node_subscriptions") or {}
    if not subscriptions:
        return
    node_registry: NodeRegistry = app_state.get("node_registry") or NodeRegistry()
    app_state["node_registry"] = node_registry
    tasks = []
    for node_id, keys in subscriptions.items():
        if session_key in keys:
            tasks.append(node_registry.send_event(node_id=node_id, event="chat", payload=payload))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _run_node_agent_request(
    *,
    node_id: str,
    payload_value: Any,
    emit_event: Callable[[str, Any], Awaitable[None]] | None,
) -> tuple[bool, str]:
    if not isinstance(payload_value, dict):
        return False, "payload object required"
    text = str(payload_value.get("text") or payload_value.get("message") or "").strip()
    if not text:
        return False, "message/text required"
    if len(text) > 20000:
        return False, "message too long"
    session_key = str(payload_value.get("sessionKey") or f"node-{node_id}").strip() or f"node-{node_id}"
    agent_id = payload_value.get("agentId") or payload_value.get("agent_id")
    agent = _resolve_agent(agent_id)
    if not agent:
        return False, "agent not initialized"
    run_id = str(payload_value.get("runId") or uuid.uuid4().hex[:12])
    _register_agent_job(run_id, session_key=session_key)
    if emit_event:
        await emit_event(
            "chat",
            {
                "runId": run_id,
                "sessionKey": session_key,
                "state": "delta",
                "message": {"role": "assistant", "content": [{"type": "text", "text": ""}]},
                "source": "node-event",
                "nodeId": node_id,
            },
        )
    try:
        response_text = await agent.process_direct(
            content=text,
            session_key=session_key,
            channel="node",
            chat_id=node_id,
        )
    except Exception as e:
        _complete_agent_job(run_id, status="error", error=str(e))
        raise
    _complete_agent_job(run_id, status="ok")
    final_payload = {
        "runId": run_id,
        "sessionKey": session_key,
        "state": "final",
        "message": {"role": "assistant", "content": [{"type": "text", "text": response_text or ""}]},
        "source": "node-event",
        "nodeId": node_id,
    }
    if emit_event:
        await emit_event("chat", final_payload)
    await _fanout_chat_to_subscribed_nodes(session_key=session_key, payload=final_payload)
    return True, ""


def _empty_usage_totals() -> dict[str, Any]:
    return {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "totalTokens": 0,
        "totalCost": 0,
        "inputCost": 0,
        "outputCost": 0,
        "cacheReadCost": 0,
        "cacheWriteCost": 0,
        "missingCostEntries": 0,
    }


def _build_agents_list_payload(config: Any) -> dict[str, Any]:
    agents = []
    for row in config.get_agent_list_for_api():
        agents.append(
            {
                "id": row.get("id"),
                "name": row.get("name") or row.get("id"),
                "identity": {
                    "name": row.get("name") or row.get("id"),
                    "emoji": "🤖",
                    "avatar": "bot",
                },
            }
        )
    return {
        "defaultId": config.get_default_agent_id(),
        "mainKey": "main",
        "scope": "global",
        "agents": agents,
    }


def _build_sessions_list_payload(agent: Any, config: Any) -> dict[str, Any]:
    rows = []
    for s in agent.sessions.list_sessions():
        key = str(s.get("key") or "")
        rows.append(
            {
                "key": key,
                "kind": "direct",
                "label": key,
                "displayName": key,
                "updatedAt": int(time.time() * 1000),
                "sessionId": key,
            }
        )
    return {
        "ts": _now_ms(),
        "path": str(get_config_path()),
        "count": len(rows),
        "defaults": {
            "model": config._resolve_default_entry().model,
            "contextTokens": None,
        },
        "sessions": rows,
    }


def _build_chat_history_payload(session: Any, limit: int = 200) -> dict[str, Any]:
    out = []
    for m in session.messages[-limit:]:
        out.append(
            {
                "role": m.get("role", "assistant"),
                "content": [{"type": "text", "text": m.get("content", "")}],
                "timestamp": m.get("timestamp"),
            }
        )
    return {"messages": out, "thinkingLevel": None}


def _normalize_presence_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "instanceId": entry.get("instance_id"),
        "host": entry.get("host"),
        "ip": entry.get("ip"),
        "version": entry.get("version"),
        "deviceFamily": entry.get("device_family"),
        "modelIdentifier": entry.get("model_identifier"),
        "mode": entry.get("mode"),
        "lastInputSeconds": entry.get("last_input_seconds"),
        "reason": entry.get("reason"),
        "ts": entry.get("ts"),
    }


def _build_config_snapshot(config: Any) -> dict[str, Any]:
    path = get_config_path()
    raw = path.read_text(encoding="utf-8") if path.exists() else "{}"
    cfg = config.model_dump(by_alias=True)
    raw_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return {
        "path": str(path),
        "exists": path.exists(),
        "raw": raw,
        "hash": raw_hash,
        "parsed": cfg,
        "valid": True,
        "config": cfg,
        "issues": [],
    }


def _build_config_schema_payload() -> dict[str, Any]:
    # Minimal JSON Schema payload for UI form fallback; raw mode remains available.
    return {
        "schema": {
            "type": "object",
            "properties": {
                "agents": {"type": "object"},
                "channels": {"type": "object"},
                "providers": {"type": "object"},
                "auth": {"type": "object"},
                "gateway": {"type": "object"},
                "tools": {"type": "object"},
                "skills": {"type": "object"},
                "plugins": {"type": "object"},
                "wallet": {"type": "object"},
            },
            "additionalProperties": True,
        },
        "uiHints": {},
        "version": "joyhousebot-rpc-1",
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _apply_config_from_raw(raw: str) -> tuple[bool, str | None]:
    try:
        data = json.loads(raw)
        from joyhousebot.config.loader import convert_keys
        from joyhousebot.config.schema import Config

        cfg = Config.model_validate(convert_keys(data))
        save_config(cfg)
        app_state["config"] = cfg
        return True, None
    except Exception as e:
        return False, str(e)


# ---------- Control helpers (overview, channels, alerts, actions) ----------

def _build_channels_status_snapshot(config: Any, channel_manager: Any) -> dict[str, Any]:
    return service_build_channels_status_snapshot(config=config, channel_manager=channel_manager, now_ms=_now_ms)


# worker status
def _load_control_plane_worker_status() -> dict[str, Any]:
    status = _load_persistent_state("control_plane.worker_status", {})
    return status if isinstance(status, dict) else {    }


# operational alerts (channels, cron, control plane)
def _build_operational_alerts(
    *,
    auth_profiles: dict[str, Any],
    channels_snapshot: dict[str, Any],
    cron_status: dict[str, Any] | None,
    control_plane_status: dict[str, Any],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = list(build_auth_profile_alerts(auth_profiles))
    now_ms = _now_ms()

    channels = channels_snapshot.get("channels", {}) if isinstance(channels_snapshot, dict) else {}
    configured_names = [name for name, st in channels.items() if isinstance(st, dict) and bool(st.get("configured"))]
    not_running = [name for name in configured_names if not bool(channels.get(name, {}).get("running"))]
    if not_running:
        all_down = len(not_running) == len(configured_names)
        alerts.append(
            {
                "source": "channels",
                "category": "availability",
                "level": "critical" if all_down else "warning",
                "severity": "critical" if all_down else "warning",
                "code": "CHANNELS_UNAVAILABLE_ALL" if all_down else "CHANNELS_UNAVAILABLE_PARTIAL",
                "title": "Channels unavailable" if all_down else "Some channels unavailable",
                "message": (
                    "All configured channels are not running."
                    if all_down
                    else f"Unavailable channels: {', '.join(not_running)}"
                ),
                "channels": not_running,
            }
        )

    if isinstance(cron_status, dict):
        enabled = bool(cron_status.get("enabled"))
        jobs = int(cron_status.get("jobs") or 0)
        next_wake = cron_status.get("next_wake_at_ms")
        if enabled and jobs > 0 and not next_wake:
            alerts.append(
                {
                    "source": "cron",
                    "category": "scheduler",
                    "level": "warning",
                    "severity": "warning",
                    "code": "CRON_SCHEDULER_STALLED",
                    "title": "Cron scheduler stalled",
                    "message": "Cron is enabled with active jobs but next wake time is missing.",
                }
            )

    if isinstance(control_plane_status, dict) and control_plane_status:
        if bool(control_plane_status.get("running")):
            updated_at = int(control_plane_status.get("updatedAtMs") or 0)
            if updated_at > 0 and (now_ms - updated_at) > 120_000:
                alerts.append(
                    {
                        "source": "control_plane",
                        "category": "worker",
                        "level": "critical",
                        "severity": "critical",
                        "code": "CONTROL_PLANE_WORKER_STALE",
                        "title": "Control plane worker heartbeat stale",
                        "message": "Worker status has not been updated for over 120 seconds.",
                        "lastUpdateMs": updated_at,
                        "server": control_plane_status.get("server"),
                        "houseId": control_plane_status.get("houseId"),
                    }
                )
            heartbeat_backoff_until = int(control_plane_status.get("heartbeatBackoffUntilMs") or 0)
            if heartbeat_backoff_until > now_ms:
                alerts.append(
                    {
                        "source": "control_plane",
                        "category": "heartbeat",
                        "level": "warning",
                        "severity": "warning",
                        "code": "CONTROL_PLANE_HEARTBEAT_BACKOFF",
                        "title": "Control plane heartbeat in backoff",
                        "message": str(control_plane_status.get("lastHeartbeatError") or "heartbeat retry backoff active"),
                        "untilMs": heartbeat_backoff_until,
                        "server": control_plane_status.get("server"),
                        "houseId": control_plane_status.get("houseId"),
                    }
                )
            claim_backoff_until = int(control_plane_status.get("claimBackoffUntilMs") or 0)
            if claim_backoff_until > now_ms:
                alerts.append(
                    {
                        "source": "control_plane",
                        "category": "claim",
                        "level": "warning",
                        "severity": "warning",
                        "code": "CONTROL_PLANE_CLAIM_BACKOFF",
                        "title": "Control plane claim in backoff",
                        "message": str(control_plane_status.get("lastClaimError") or "claim retry backoff active"),
                        "untilMs": claim_backoff_until,
                        "server": control_plane_status.get("server"),
                        "houseId": control_plane_status.get("houseId"),
                    }
                )

    return alerts


def _alert_priority(level: str) -> int:
    lv = (level or "").strip().lower()
    if lv == "critical":
        return 200
    if lv == "warning":
        return 100
    return 0


# alert code profile & action validation (catalog, validate, validate-batch)
ALERT_CODE_PROFILE: dict[str, dict[str, Any]] = {
    "AUTH_PROFILES_DOWN": {
        "canonicalCode": "AUTH.UNAVAILABLE.ALL",
        "aliases": ["openclaw.auth.unavailable.all", "auth_profiles_down"],
        "action": {"type": "navigate", "name": "openPage", "target": "settings.auth", "params": {"tab": "profiles"}},
        "executionPolicy": {"riskLevel": "low", "confirmRequired": False, "safeInReadonly": True, "requiresScope": "operator.read"},
    },
    "AUTH_PROFILES_DEGRADED": {
        "canonicalCode": "AUTH.UNAVAILABLE.PARTIAL",
        "aliases": ["openclaw.auth.unavailable.partial", "auth_profiles_degraded"],
        "action": {"type": "navigate", "name": "openPage", "target": "settings.auth", "params": {"tab": "profiles"}},
        "executionPolicy": {"riskLevel": "low", "confirmRequired": False, "safeInReadonly": True, "requiresScope": "operator.read"},
    },
    "AUTH_PROVIDER_DOWN": {
        "canonicalCode": "AUTH.PROVIDER.DOWN",
        "aliases": ["openclaw.auth.provider.down"],
        "action": {"type": "navigate", "name": "openPage", "target": "settings.auth.provider"},
        "executionPolicy": {"riskLevel": "low", "confirmRequired": False, "safeInReadonly": True, "requiresScope": "operator.read"},
    },
    "AUTH_PROVIDER_DEGRADED": {
        "canonicalCode": "AUTH.PROVIDER.DEGRADED",
        "aliases": ["openclaw.auth.provider.degraded"],
        "action": {"type": "navigate", "name": "openPage", "target": "settings.auth.provider"},
        "executionPolicy": {"riskLevel": "low", "confirmRequired": False, "safeInReadonly": True, "requiresScope": "operator.read"},
    },
    "CHANNELS_UNAVAILABLE_ALL": {
        "canonicalCode": "CHANNELS.UNAVAILABLE.ALL",
        "aliases": ["openclaw.channels.unavailable.all"],
        "action": {
            "type": "run_command",
            "name": "diagnoseChannels",
            "command": "joyhousebot",
            "args": ["channels", "status"],
        },
        "executionPolicy": {"riskLevel": "medium", "confirmRequired": True, "safeInReadonly": False, "requiresScope": "operator.write"},
    },
    "CHANNELS_UNAVAILABLE_PARTIAL": {
        "canonicalCode": "CHANNELS.UNAVAILABLE.PARTIAL",
        "aliases": ["openclaw.channels.unavailable.partial"],
        "action": {
            "type": "run_command",
            "name": "diagnoseChannels",
            "command": "joyhousebot",
            "args": ["channels", "status"],
        },
        "executionPolicy": {"riskLevel": "medium", "confirmRequired": True, "safeInReadonly": False, "requiresScope": "operator.write"},
    },
    "CRON_SCHEDULER_STALLED": {
        "canonicalCode": "CRON.SCHEDULER.STALLED",
        "aliases": ["openclaw.cron.scheduler.stalled"],
        "action": {
            "type": "open_url",
            "name": "openCronOverview",
            "url": "/control/overview",
        },
        "executionPolicy": {"riskLevel": "low", "confirmRequired": False, "safeInReadonly": True, "requiresScope": "operator.read"},
    },
    "CONTROL_PLANE_WORKER_STALE": {
        "canonicalCode": "CONTROL_PLANE.WORKER.STALE",
        "aliases": ["openclaw.control_plane.worker.stale"],
        "action": {
            "type": "run_command",
            "name": "restartBotWorker",
            "command": "joyhousebot",
            "args": ["bot", "worker"],
        },
        "executionPolicy": {"riskLevel": "high", "confirmRequired": True, "safeInReadonly": False, "requiresScope": "operator.write"},
    },
    "CONTROL_PLANE_HEARTBEAT_BACKOFF": {
        "canonicalCode": "CONTROL_PLANE.HEARTBEAT.BACKOFF",
        "aliases": ["openclaw.control_plane.heartbeat.backoff"],
        "action": {
            "type": "run_command",
            "name": "inspectBotWorker",
            "command": "joyhousebot",
            "args": ["bot", "worker", "--run-once"],
        },
        "executionPolicy": {"riskLevel": "medium", "confirmRequired": True, "safeInReadonly": False, "requiresScope": "operator.write"},
    },
    "CONTROL_PLANE_CLAIM_BACKOFF": {
        "canonicalCode": "CONTROL_PLANE.CLAIM.BACKOFF",
        "aliases": ["openclaw.control_plane.claim.backoff"],
        "action": {
            "type": "run_command",
            "name": "inspectBotWorker",
            "command": "joyhousebot",
            "args": ["bot", "worker", "--run-once"],
        },
        "executionPolicy": {"riskLevel": "medium", "confirmRequired": True, "safeInReadonly": False, "requiresScope": "operator.write"},
    },
}


def _build_action_schema(action: dict[str, Any]) -> dict[str, Any]:
    action_type = str(action.get("type") or "none")
    if action_type == "navigate":
        return {
            "type": "object",
            "required": ["type", "name", "target"],
            "properties": {
                "type": {"type": "string", "enum": ["navigate"]},
                "name": {"type": "string"},
                "target": {"type": "string"},
                "params": {"type": "object"},
            },
        }
    if action_type == "run_command":
        return {
            "type": "object",
            "required": ["type", "name", "command", "args"],
            "properties": {
                "type": {"type": "string", "enum": ["run_command"]},
                "name": {"type": "string"},
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "params": {"type": "object"},
            },
        }
    if action_type == "open_url":
        return {
            "type": "object",
            "required": ["type", "name", "url"],
            "properties": {
                "type": {"type": "string", "enum": ["open_url"]},
                "name": {"type": "string"},
                "url": {"type": "string"},
                "params": {"type": "object"},
            },
        }
    return {
        "type": "object",
        "required": ["type"],
        "properties": {"type": {"type": "string", "enum": ["none"]}, "params": {"type": "object"}},
    }


def _build_action_validation_rule(action: dict[str, Any]) -> dict[str, Any]:
    action_type = str(action.get("type") or "none")
    if action_type == "navigate":
        return {
            "mode": "exact_fields",
            "type": "navigate",
            "target": str(action.get("target") or ""),
        }
    if action_type == "run_command":
        return {
            "mode": "command_whitelist",
            "type": "run_command",
            "command": str(action.get("command") or "joyhousebot"),
            "argsPrefix": [str(x) for x in list(action.get("args") or [])],
            "allowExtraFlags": ["--server"],
        }
    if action_type == "open_url":
        return {
            "mode": "exact_fields",
            "type": "open_url",
            "url": str(action.get("url") or "/control/overview"),
        }
    return {"mode": "none", "type": "none"}


def _validate_action_candidate(
    *,
    code: str,
    candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = ALERT_CODE_PROFILE.get(code) if isinstance(code, str) else None
    if not isinstance(profile, dict):
        return {"ok": False, "reason": "unknown_code", "normalizedAction": None}
    expected_action = dict(profile.get("action") or {})
    rule = _build_action_validation_rule(expected_action)
    actual = dict(candidate or expected_action)
    actual_type = str(actual.get("type") or "")
    expected_type = str(rule.get("type") or "")
    if actual_type != expected_type:
        return {"ok": False, "reason": "type_mismatch", "normalizedAction": None, "rule": rule}
    if expected_type == "navigate":
        if str(actual.get("target") or "") != str(rule.get("target") or ""):
            return {"ok": False, "reason": "target_mismatch", "normalizedAction": None, "rule": rule}
    elif expected_type == "open_url":
        if str(actual.get("url") or "") != str(rule.get("url") or ""):
            return {"ok": False, "reason": "url_mismatch", "normalizedAction": None, "rule": rule}
    elif expected_type == "run_command":
        if str(actual.get("command") or "") != str(rule.get("command") or ""):
            return {"ok": False, "reason": "command_mismatch", "normalizedAction": None, "rule": rule}
        expected_prefix = [str(x) for x in list(rule.get("argsPrefix") or [])]
        actual_args = [str(x) for x in list(actual.get("args") or [])]
        if len(actual_args) < len(expected_prefix) or actual_args[: len(expected_prefix)] != expected_prefix:
            return {"ok": False, "reason": "args_prefix_mismatch", "normalizedAction": None, "rule": rule}
        allowed_extras = set(str(x) for x in list(rule.get("allowExtraFlags") or []))
        extras = actual_args[len(expected_prefix) :]
        i = 0
        while i < len(extras):
            token = extras[i]
            if token in allowed_extras:
                i += 2 if token == "--server" else 1
                continue
            return {"ok": False, "reason": f"extra_arg_not_allowed:{token}", "normalizedAction": None, "rule": rule}
    normalized = dict(expected_action)
    if isinstance(actual.get("params"), dict):
        normalized["params"] = dict(actual.get("params") or {})
    return {"ok": True, "reason": "ok", "normalizedAction": normalized, "rule": rule}


def _validate_action_batch(items: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    valid = 0
    invalid = 0
    for idx, row in enumerate(items):
        code = str(row.get("code") or "").strip()
        action = row.get("action")
        result = _validate_action_candidate(code=code, candidate=action if isinstance(action, dict) else None)
        if bool(result.get("ok")):
            valid += 1
        else:
            invalid += 1
        results.append(
            {
                "index": idx,
                "code": code,
                **result,
            }
        )
    return {
        "ok": invalid == 0,
        "total": len(items),
        "valid": valid,
        "invalid": invalid,
        "results": results,
    }


def _build_actions_catalog() -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    for code, profile in ALERT_CODE_PROFILE.items():
        canonical = str(profile.get("canonicalCode") or code)
        aliases = profile.get("aliases")
        action = profile.get("action")
        actions.append(
            {
                "code": code,
                "canonicalCode": canonical,
                "aliases": [str(a) for a in aliases] if isinstance(aliases, list) else [],
                "action": dict(action) if isinstance(action, dict) else {"type": "none", "name": "noop", "params": {}},
                "schema": _build_action_schema(dict(action) if isinstance(action, dict) else {"type": "none"}),
                "validationRule": _build_action_validation_rule(
                    dict(action) if isinstance(action, dict) else {"type": "none"}
                ),
                "executionPolicy": dict(profile.get("executionPolicy") or {}),
            }
        )
    actions.sort(key=lambda x: (str(x.get("canonicalCode") or ""), str(x.get("code") or "")))
    return {
        "version": 2,
        "count": len(actions),
        "supportedActionTypes": ["navigate", "run_command", "open_url", "none"],
        "supportsBatchValidate": True,
        "actions": actions,
        "generatedAtMs": _now_ms(),
    }


def _enrich_alert_code_profile(item: dict[str, Any]) -> None:
    code = str(item.get("code") or "")
    profile = ALERT_CODE_PROFILE.get(code, {})
    canonical = str(profile.get("canonicalCode") or code or "UNKNOWN")
    aliases = profile.get("aliases")
    action = profile.get("action")
    execution_policy = profile.get("executionPolicy")
    item["canonicalCode"] = canonical
    if isinstance(aliases, list):
        item["aliases"] = [str(a) for a in aliases if str(a).strip()]
    else:
        item["aliases"] = []
    if isinstance(action, dict):
        action_payload = dict(action)
    else:
        action_payload = {"type": "none", "name": "noop", "params": {}}
    action_type = str(action_payload.get("type") or "none")
    if action_type == "navigate":
        params = dict(action_payload.get("params") or {})
        if item.get("provider"):
            params.setdefault("provider", str(item.get("provider")))
        if isinstance(item.get("channels"), list):
            params.setdefault("channels", list(item.get("channels") or []))
        if item.get("untilMs") is not None:
            params.setdefault("untilMs", int(item.get("untilMs") or 0))
        if item.get("nextRecoveryMs") is not None:
            params.setdefault("nextRecoveryMs", int(item.get("nextRecoveryMs") or 0))
        action_payload.setdefault("name", "openPage")
        action_payload["params"] = params
    elif action_type == "run_command":
        params = dict(action_payload.get("params") or {})
        if item.get("provider"):
            params.setdefault("provider", str(item.get("provider")))
        if item.get("server"):
            params.setdefault("server", str(item.get("server")))
        if item.get("houseId"):
            params.setdefault("houseId", str(item.get("houseId")))
        if isinstance(item.get("channels"), list):
            params.setdefault("channels", list(item.get("channels") or []))
        if item.get("untilMs") is not None:
            params.setdefault("untilMs", int(item.get("untilMs") or 0))
        args = list(action_payload.get("args") or [])
        if params.get("server") and "--server" not in args:
            args.extend(["--server", str(params["server"])])
        action_payload.setdefault("name", "runCommand")
        action_payload.setdefault("command", "joyhousebot")
        action_payload["args"] = [str(x) for x in args]
        action_payload["params"] = params
    elif action_type == "open_url":
        params = dict(action_payload.get("params") or {})
        if item.get("provider"):
            params.setdefault("provider", str(item.get("provider")))
        if item.get("untilMs") is not None:
            params.setdefault("untilMs", int(item.get("untilMs") or 0))
        action_payload.setdefault("name", "openUrl")
        action_payload.setdefault("url", "/control/overview")
        action_payload["params"] = params
    else:
        action_payload.setdefault("params", {})
    item["action"] = action_payload
    item["actionSchema"] = _build_action_schema(action_payload)
    item["executionPolicy"] = (
        dict(execution_policy)
        if isinstance(execution_policy, dict)
        else {"riskLevel": "low", "confirmRequired": False, "safeInReadonly": True, "requiresScope": "operator.read"}
    )


def _build_alert_dedupe_key(alert: dict[str, Any]) -> str:
    source = str(alert.get("source") or "unknown")
    category = str(alert.get("category") or "general")
    code = str(alert.get("code") or "UNKNOWN")
    provider = str(alert.get("provider") or "")
    return f"{source}:{category}:{code}:{provider}"


# alerts normalization & lifecycle (alerts-lifecycle view)
def _normalize_operational_alerts(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for raw in alerts:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        level = str(item.get("level") or item.get("severity") or "warning").lower()
        item["level"] = level
        item["severity"] = level
        item["source"] = str(item.get("source") or "unknown")
        item["category"] = str(item.get("category") or "general")
        item["group"] = f"{item['source']}.{item['category']}"
        item["priority"] = _alert_priority(level)
        _enrich_alert_code_profile(item)
        item["dedupeKey"] = _build_alert_dedupe_key(item)
        existing = deduped.get(item["dedupeKey"])
        if existing is None or int(item["priority"]) > int(existing.get("priority") or 0):
            deduped[item["dedupeKey"]] = item
    result = list(deduped.values())
    result.sort(
        key=lambda a: (
            -int(a.get("priority") or 0),
            str(a.get("source") or ""),
            str(a.get("category") or ""),
            str(a.get("code") or ""),
            str(a.get("provider") or ""),
        )
    )
    return result


def _load_alerts_lifecycle_state() -> dict[str, Any]:
    raw = _load_persistent_state("rpc.alerts_lifecycle", {"active": {}, "resolvedRecent": [], "lastUpdatedMs": 0})
    if not isinstance(raw, dict):
        return {"active": {}, "resolvedRecent": [], "lastUpdatedMs": 0}
    active = raw.get("active", {})
    resolved = raw.get("resolvedRecent", [])
    return {
        "active": active if isinstance(active, dict) else {},
        "resolvedRecent": resolved if isinstance(resolved, list) else [],
        "lastUpdatedMs": int(raw.get("lastUpdatedMs") or 0),
    }


def _save_alerts_lifecycle_state(state: dict[str, Any]) -> None:
    _save_persistent_state("rpc.alerts_lifecycle", state)


def _apply_alerts_lifecycle(alerts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now = _now_ms()
    state = _load_alerts_lifecycle_state()
    active_map = dict(state.get("active") or {})
    resolved_recent = list(state.get("resolvedRecent") or [])
    current_keys: set[str] = set()
    enriched: list[dict[str, Any]] = []

    for alert in alerts:
        item = dict(alert)
        key = str(item.get("dedupeKey") or _build_alert_dedupe_key(item))
        current_keys.add(key)
        rec = active_map.get(key, {})
        first_seen = int(rec.get("firstSeenMs") or now)
        last_transition = int(rec.get("lastTransitionMs") or first_seen)
        if rec.get("active") is False:
            last_transition = now
        lifecycle_row = {
            "dedupeKey": key,
            "code": item.get("code"),
            "canonicalCode": item.get("canonicalCode"),
            "source": item.get("source"),
            "category": item.get("category"),
            "level": item.get("level"),
            "firstSeenMs": first_seen,
            "lastSeenMs": now,
            "lastTransitionMs": last_transition,
            "resolvedAtMs": None,
            "active": True,
        }
        active_map[key] = lifecycle_row
        item.update(
            {
                "firstSeenMs": first_seen,
                "lastSeenMs": now,
                "lastTransitionMs": last_transition,
                "resolvedAtMs": None,
                "active": True,
            }
        )
        enriched.append(item)

    for key in list(active_map.keys()):
        if key in current_keys:
            continue
        rec = dict(active_map.pop(key))
        rec["active"] = False
        rec["resolvedAtMs"] = now
        rec["lastTransitionMs"] = now
        resolved_recent.insert(0, rec)

    resolved_recent = resolved_recent[:200]
    new_state = {"active": active_map, "resolvedRecent": resolved_recent, "lastUpdatedMs": now}
    _save_alerts_lifecycle_state(new_state)

    lifecycle = {
        "activeCount": len(active_map),
        "resolvedRecentCount": len(resolved_recent),
        "active": sorted(list(active_map.values()), key=lambda r: str(r.get("dedupeKey") or "")),
        "resolvedRecent": resolved_recent[:50],
        "lastUpdatedMs": now,
    }
    return enriched, lifecycle


def _get_alerts_lifecycle_view() -> dict[str, Any]:
    state = _load_alerts_lifecycle_state()
    active_map = dict(state.get("active") or {})
    resolved_recent = list(state.get("resolvedRecent") or [])
    return {
        "activeCount": len(active_map),
        "resolvedRecentCount": len(resolved_recent),
        "active": sorted(list(active_map.values()), key=lambda r: str(r.get("dedupeKey") or "")),
        "resolvedRecent": resolved_recent[:50],
        "lastUpdatedMs": int(state.get("lastUpdatedMs") or 0),
    }


def _build_alerts_summary(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    critical = 0
    warning = 0
    by_source: dict[str, dict[str, int | str]] = {}
    for alert in alerts:
        level = str(alert.get("level") or "").lower()
        if level == "critical":
            critical += 1
        elif level == "warning":
            warning += 1
        source = str(alert.get("source") or "unknown")
        row = by_source.setdefault(source, {"source": source, "critical": 0, "warning": 0, "total": 0})
        row["total"] = int(row["total"]) + 1
        if level == "critical":
            row["critical"] = int(row["critical"]) + 1
        elif level == "warning":
            row["warning"] = int(row["warning"]) + 1
    return {
        "total": len(alerts),
        "critical": critical,
        "warning": warning,
        "bySource": sorted(by_source.values(), key=lambda x: str(x["source"])),
    }


# ---------- End control helpers ----------

def _build_skills_status_report(config: Any) -> dict[str, Any]:
    return build_skills_status_report_from_service(config)


def _get_store() -> LocalStateStore:
    return LocalStateStore.default()


def _persist_trace(run_id: str, session_key: str, status: str, error: str | None) -> None:
    """Read trace recorder from context, write to store, clear context. Used after RPC agent run."""
    from joyhousebot.services.chat.trace_context import trace_recorder

    rec = trace_recorder.get()
    if not rec:
        return
    try:
        ended_ms = _now_ms()
        store = _get_store()
        store.insert_agent_trace(
            trace_id=run_id,
            session_key=session_key,
            status=status,
            started_at_ms=rec.started_at_ms,
            ended_at_ms=ended_ms,
            error_text=error,
            steps_json=rec.to_steps_json(),
            tools_used=rec.to_tools_used_json(),
            message_preview=rec.message_preview or None,
        )
    finally:
        trace_recorder.set(None)


def _load_persistent_state(name: str, default: Any) -> Any:
    try:
        return _get_store().get_sync_json(name=name, default=default)
    except Exception:
        return default


def _save_persistent_state(name: str, value: Any) -> None:
    try:
        if isinstance(value, (dict, list)):
            _get_store().set_sync_json(name=name, value=value)
    except Exception:
        pass


def _estimate_tokens(text: str) -> int:
    # Lightweight estimate for usage dashboards when provider usage is unavailable.
    return max(0, (len(text or "") + 3) // 4)


async def _run_update_install() -> None:
    """Run self-update in background and persist status."""
    status = app_state.get("rpc_update_status") or {}
    status.update({"running": True, "startedAtMs": _now_ms(), "finishedAtMs": None, "ok": None, "message": "starting"})
    app_state["rpc_update_status"] = status
    _save_persistent_state("rpc.update_status", status)
    cmd = [
        os.environ.get("PYTHON", "python"),
        "-m",
        "pip",
        "install",
        "--upgrade",
        "joyhousebot",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        ok = proc.returncode == 0
        msg = (out.decode("utf-8", errors="ignore") + "\n" + err.decode("utf-8", errors="ignore")).strip()
        status.update(
            {
                "running": False,
                "finishedAtMs": _now_ms(),
                "ok": ok,
                "message": msg[-4000:],
                "returncode": proc.returncode,
            }
        )
    except Exception as e:
        status.update({"running": False, "finishedAtMs": _now_ms(), "ok": False, "message": str(e)})
    app_state["rpc_update_status"] = status
    _save_persistent_state("rpc.update_status", status)


def _session_usage_entry(key: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Build usage entry for a session; aligns with OpenClaw SessionUsageEntry / SessionCostSummary."""
    user = 0
    assistant = 0
    tool_calls = 0
    tool_results = 0
    errors = 0
    input_tokens = 0
    output_tokens = 0
    total_cost = 0.0
    input_cost = 0.0
    output_cost = 0.0
    first_activity_ms: int | None = None
    last_activity_ms: int | None = None
    activity_dates: set[str] = set()
    # Per-day: date -> {tokens, cost, messages, toolCalls, errors}
    daily: dict[str, dict[str, Any]] = {}

    def _ts_to_ms(ts: Any) -> int | None:
        if ts is None:
            return None
        if isinstance(ts, (int, float)) and ts > 0:
            return int(ts) if ts < 1e12 else int(ts)
        if isinstance(ts, str):
            try:
                return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
            except Exception:
                return None
        return None

    def _date_from_ms(ms: int) -> str:
        dt = datetime.utcfromtimestamp(ms / 1000.0)
        return dt.strftime("%Y-%m-%d")

    for m in messages:
        role = str(m.get("role") or "")
        content = str(m.get("content") or "")
        ts_ms = _ts_to_ms(m.get("timestamp"))
        if ts_ms is not None:
            if first_activity_ms is None or ts_ms < first_activity_ms:
                first_activity_ms = ts_ms
            if last_activity_ms is None or ts_ms > last_activity_ms:
                last_activity_ms = ts_ms
            activity_dates.add(_date_from_ms(ts_ms))

        # Tokens/cost: prefer message.usage when present (persisted from LLM response)
        msg_usage = m.get("usage")
        if isinstance(msg_usage, dict):
            inp = int(msg_usage.get("input") or msg_usage.get("prompt_tokens") or 0)
            out = int(msg_usage.get("output") or msg_usage.get("completion_tokens") or 0)
            input_tokens += inp
            output_tokens += out
        else:
            inp = _estimate_tokens(content) if role == "user" else 0
            out = _estimate_tokens(content) if role == "assistant" else 0
            if role == "user":
                input_tokens += inp
            elif role == "assistant":
                output_tokens += out

        cost_val = m.get("cost")
        if isinstance(cost_val, (int, float)) and cost_val >= 0:
            total_cost += float(cost_val)
            if role == "user":
                pass  # input cost could be split if we had it
            elif role == "assistant":
                output_cost += float(cost_val)

        if role == "user":
            user += 1
        elif role == "assistant":
            assistant += 1
        elif role == "tool":
            tool_results += 1
        if m.get("tools_used"):
            tool_calls += 1
        if "error" in content.lower():
            errors += 1

        # Daily breakdown: use message date; tokens for this message
        day = _date_from_ms(ts_ms) if ts_ms is not None else ""
        if not day:
            continue
        if day not in daily:
            daily[day] = {
                "date": day,
                "tokens": 0,
                "cost": 0,
                "messages": 0,
                "toolCalls": 0,
                "errors": 0,
            }
        daily[day]["messages"] += 1
        if role == "user":
            daily[day]["tokens"] += inp if isinstance(msg_usage, dict) else _estimate_tokens(content)
        elif role == "assistant":
            daily[day]["tokens"] += out if isinstance(msg_usage, dict) else _estimate_tokens(content)
            if isinstance(cost_val, (int, float)) and cost_val >= 0:
                daily[day]["cost"] += float(cost_val)
        if m.get("tools_used"):
            daily[day]["toolCalls"] += 1
        if "error" in content.lower():
            daily[day]["errors"] += 1

    total_tokens = input_tokens + output_tokens
    now_ms = int(time.time() * 1000)
    updated_at = last_activity_ms if last_activity_ms is not None else now_ms
    daily_breakdown = [daily[d] for d in sorted(daily)]

    return {
        "key": key,
        "label": key,
        "sessionId": key,
        "updatedAt": updated_at,
        "firstActivity": first_activity_ms,
        "lastActivity": last_activity_ms,
        "activityDates": sorted(activity_dates) if activity_dates else [],
        "usage": {
            "input": input_tokens,
            "output": output_tokens,
            "cacheRead": 0,
            "cacheWrite": 0,
            "totalTokens": total_tokens,
            "totalCost": total_cost,
            "inputCost": input_cost,
            "outputCost": output_cost,
            "cacheReadCost": 0,
            "cacheWriteCost": 0,
            "missingCostEntries": 0,
            "messageCounts": {
                "total": len(messages),
                "user": user,
                "assistant": assistant,
                "toolCalls": tool_calls,
                "toolResults": tool_results,
                "errors": errors,
            },
            "dailyBreakdown": daily_breakdown,
        },
    }

@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "joyhousebot-api",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"ok": True, "service": "joyhousebot-api"}


# ---------- Control (OpenClaw-style overview / channels / cron) ----------

@api_router.get("/control/overview")
async def control_overview():
    """Overview for control UI: connection, uptime, sessions count, cron summary, channels."""
    config = app_state.get("config") or get_cached_config()
    return control_overview_response(
        app_state=app_state,
        config=config,
        presence_count=len(presence_store.list_entries()),
        now_ms=_now_ms,
        load_control_plane_worker_status=_load_control_plane_worker_status,
        build_auth_profiles_report=build_auth_profiles_report,
        build_operational_alerts=_build_operational_alerts,
        normalize_operational_alerts=_normalize_operational_alerts,
        apply_alerts_lifecycle=_apply_alerts_lifecycle,
        build_alerts_summary=_build_alerts_summary,
        build_actions_catalog=_build_actions_catalog,
    )


@api_router.get("/control/channels")
async def control_channels():
    """Channel status for control UI (enabled + running from config and runtime)."""
    config = app_state.get("config") or get_cached_config()
    return control_channels_response(config=config, channel_manager=app_state.get("channel_manager"), now_ms=_now_ms)


@api_router.get("/control/presence")
async def control_presence():
    """Presence list for control UI: connected clients + gateway (OpenClaw-style)."""
    entries = presence_store.list_entries()
    return {"ok": True, "presence": entries}


@api_router.get("/control/queue")
async def control_queue():
    """Queue metrics for control UI: lanes (sessionKey, runningRunId, queued, queueDepth, headWaitMs)."""
    return control_queue_response(app_state=app_state, now_ms=_now_ms)


@api_router.get("/traces")
async def list_traces(
    session_key: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
):
    """List agent run traces (observability). Optional session_key, limit, cursor for pagination."""
    limit = max(1, min(limit, 200))
    store = _get_store()
    items, next_cursor = store.list_agent_traces(
        session_key=session_key,
        limit=limit,
        cursor=cursor,
    )
    out = {"items": items}
    if next_cursor is not None:
        out["nextCursor"] = next_cursor
    return out


@api_router.get("/traces/{trace_id:path}")
async def get_trace(trace_id: str):
    """Get one agent run trace by id (run_id)."""
    store = _get_store()
    trace = store.get_agent_trace(trace_id.strip())
    if trace is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return trace

@api_router.get("/control/auth-profiles")
async def control_auth_profiles():
    """Auth profile and cooldown status for control UI."""
    config = app_state.get("config") or get_cached_config()
    return {"ok": True, **build_auth_profiles_report(config)}


@api_router.get("/control/actions-catalog")
async def control_actions_catalog():
    """Action catalog for alert-driven UI rendering."""
    return {"ok": True, **_build_actions_catalog()}


@api_router.post("/control/actions/validate")
async def control_actions_validate(body: ActionValidateBody):
    """Validate an action against backend whitelist rules."""
    result = _validate_action_candidate(code=str(body.code or ""), candidate=body.action or {})
    return {"ok": bool(result.get("ok")), **result}


@api_router.post("/control/actions/validate-batch")
async def control_actions_validate_batch(body: ActionValidateBatchBody):
    """Batch validate actions against backend whitelist rules."""
    items = [{"code": x.code, "action": x.action} for x in body.items]
    return _validate_action_batch(items)


@api_router.post("/control/actions/validate-batch-lifecycle")
async def control_actions_validate_batch_lifecycle(body: ActionValidateBatchBody):
    """Batch validate actions and return current lifecycle snapshot."""
    items = [{"code": x.code, "action": x.action} for x in body.items]
    validation = _validate_action_batch(items)
    overview = await control_overview()
    return {
        "ok": bool(validation.get("ok")),
        "validation": validation,
        "alertsSummary": overview.get("alertsSummary", {}),
        "alertsLifecycle": overview.get("alertsLifecycle", {}),
        "generatedAtMs": _now_ms(),
    }


@api_router.get("/control/alerts-lifecycle")
async def control_alerts_lifecycle():
    """Alert lifecycle status (active + recently resolved)."""
    return {"ok": True, **_get_alerts_lifecycle_view()}


@api_router.get("/skills")
async def list_skills_api():
    """List all skills with name, source, description, available, enabled."""
    return list_skills_response(config=get_cached_config())


@api_router.patch("/skills/{name}")
async def patch_skill(name: str, body: SkillPatchBody):
    """Update skill enabled state."""
    return patch_skill_response(
        name=name,
        enabled=body.enabled,
        get_cached_config=get_cached_config,
        save_config=save_config,
        app_state=app_state,
    )


@api_router.get("/house/identity")
async def get_house_identity():
    """Get house identity info."""
    from joyhousebot.storage import LocalStateStore
    store = LocalStateStore.default()
    return get_house_identity_response(store=store)


@api_router.post("/house/register")
async def register_house(body: dict[str, Any]):
    """Register house to backend."""
    from joyhousebot.storage import LocalStateStore
    store = LocalStateStore.default()
    return register_house_response(store=store, body=body)


@api_router.post("/house/bind")
async def bind_house(body: dict[str, Any]):
    """Bind house to a user."""
    from joyhousebot.storage import LocalStateStore
    store = LocalStateStore.default()
    return bind_house_response(store=store, body=body)


@api_router.get("/cloud-connect/status")
async def get_cloud_connect_status():
    """Get cloud connection status."""
    from joyhousebot.storage import LocalStateStore
    store = LocalStateStore.default()
    return get_cloud_connect_status_response(store=store)


@api_router.post("/cloud-connect/start")
async def start_cloud_connect(body: dict[str, Any] | None = None):
    """Start cloud connect worker."""
    return start_cloud_connect_response(body=body)


@api_router.post("/cloud-connect/stop")
async def stop_cloud_connect(body: dict[str, Any] | None = None):
    """Stop cloud connect worker."""
    return stop_cloud_connect_response(body=body)


@api_router.get("/agent")
async def get_agent():
    """Get default agent info (backward compat). Prefer GET /agents for multi-agent."""
    return get_agent_response(config=get_cached_config())


@api_router.get("/agents")
async def list_agents():
    """List all agents (OpenClaw-style multi-agent)."""
    return list_agents_response(config=get_cached_config())


@api_router.patch("/agents/{agent_id}")
async def patch_agent(agent_id: str, body: AgentPatchBody):
    """Update one agent (e.g. activated for chat page)."""
    return patch_agent_response(
        agent_id=agent_id,
        activated=body.activated,
        get_cached_config=get_cached_config,
        save_config=save_config,
        app_state=app_state,
    )


@api_router.get("/config")
async def handle_get_config():
    """Get current configuration."""
    config = get_cached_config()
    return get_config_response(config=config, get_wallet_from_store=_get_wallet_from_store)


def _get_wallet_from_store():
    """Wallet state from SQLite (source of truth); fallback to config file if no store."""
    from joyhousebot.identity.wallet_store import wallet_file_exists, get_wallet_address
    if wallet_file_exists():
        return {"enabled": True, "address": get_wallet_address() or ""}
    config = get_cached_config()
    return {"enabled": config.wallet.enabled, "address": config.wallet.address or ""}


@api_router.put("/config")
async def update_config(update: ConfigUpdate):
    """Update configuration."""
    try:
        return update_config_response(
            update=update,
            get_cached_config=get_cached_config,
            save_config=save_config,
            app_state=app_state,
            get_wallet_from_store=_get_wallet_from_store,
            log_plugin_reload_warning=lambda msg: logger.warning(
                "Plugin reload after config update failed: {}", msg
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update config: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


@api_router.post("/chat")
async def chat(msg: ChatMessage):
    """Send a message to the agent (optional agent_id for multi-agent)."""
    agent = resolve_agent_or_503(agent_id=msg.agent_id, resolve_agent=_resolve_agent)
    config = get_cached_config()
    return await build_chat_response(
        agent=agent,
        message=msg.message,
        session_id=msg.session_id,
        log_error=logger.error,
        error_detail=unknown_error_detail,
        config=config,
        check_abort_requested=_check_abort_requested,
    )


@api_router.post("/message/send")
async def message_send(body: DirectMessageBody):
    """Send outbound message directly to a channel target via gateway dispatcher."""
    bus, channel, target, msg = prepare_direct_message_send(
        body=body,
        app_state=app_state,
    )
    return await publish_direct_message(
        bus=bus,
        channel=channel,
        target=target,
        msg=msg,
        message_text=body.message,
        logger_error=logger.error,
    )


@api_router.get("/sessions")
async def list_sessions(agent_id: str | None = None):
    """List all conversation sessions (optional agent_id for multi-agent)."""
    agent = resolve_agent_or_503(agent_id=agent_id, resolve_agent=_resolve_agent)
    try:
        return list_sessions_response(agent=agent)
    except Exception as e:
        logger.error(f"List sessions error: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


@api_router.get("/sessions/{session_key:path}/history")
async def get_session_history(session_key: str, agent_id: str | None = None):
    """Get chat history for a session (optional agent_id for multi-agent)."""
    agent = resolve_agent_or_503(agent_id=agent_id, resolve_agent=_resolve_agent)
    try:
        return get_session_history_response(agent=agent, session_key=session_key)
    except Exception as e:
        logger.error(f"Session history error: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


@api_router.delete("/sessions/{session_key:path}")
async def delete_session(session_key: str, agent_id: str | None = None):
    """Delete a session and its history (optional agent_id for multi-agent)."""
    agent = resolve_agent_or_503(agent_id=agent_id, resolve_agent=_resolve_agent)
    try:
        return delete_session_response(agent=agent, session_key=session_key)
    except Exception as e:
        logger.error(f"Delete session error: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


# ---------- Cron (OpenClaw-style scheduled tasks) ----------

class CronScheduleBody(BaseModel):
    kind: str  # "at" | "every" | "cron"
    at_ms: int | None = None
    every_ms: int | None = None
    every_seconds: int | None = None  # alternative to every_ms
    expr: str | None = None
    tz: str | None = None


class CronJobCreate(BaseModel):
    name: str
    schedule: CronScheduleBody
    message: str = ""
    deliver: bool = False
    channel: str | None = None
    to: str | None = None
    delete_after_run: bool = False
    agent_id: str | None = None  # OpenClaw: which agent runs this job; None = default
    payload_kind: str = "agent_turn"  # agent_turn | memory_compaction


class CronJobPatch(BaseModel):
    enabled: bool | None = None


@api_router.get("/cron/jobs")
async def cron_list_jobs(include_disabled: bool = True):
    """List cron jobs (include_disabled=false to show only enabled)."""
    cron_service = app_state.get("cron_service")
    if not cron_service:
        return {"ok": True, "jobs": [], "message": "Cron not available (run gateway for cron)"}
    try:
        return list_cron_jobs_response(
            cron_service=cron_service,
            include_disabled=include_disabled,
            job_to_dict=cron_job_to_dict,
        )
    except Exception as e:
        logger.error(f"Cron list error: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


@api_router.post("/cron/jobs")
async def cron_add_job(body: CronJobCreate):
    """Add a cron job."""
    cron_service = app_state.get("cron_service")
    if not cron_service:
        raise HTTPException(status_code=503, detail="Cron not available (run gateway for cron)")
    try:
        return add_cron_job_response(
            cron_service=cron_service,
            body=body,
            schedule_body_to_internal=schedule_body_to_internal,
            job_to_dict=cron_job_to_dict,
        )
    except Exception as e:
        logger.error(f"Cron add error: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


@api_router.patch("/cron/jobs/{job_id}")
async def cron_patch_job(job_id: str, body: CronJobPatch):
    """Enable or disable a cron job."""
    cron_service = app_state.get("cron_service")
    if not cron_service:
        raise HTTPException(status_code=503, detail="Cron not available (run gateway for cron)")
    try:
        return patch_cron_job_response(
            cron_service=cron_service,
            job_id=job_id,
            body=body,
            job_to_dict=cron_job_to_dict,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cron patch error: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


@api_router.delete("/cron/jobs/{job_id}")
async def cron_delete_job(job_id: str):
    """Remove a cron job."""
    cron_service = app_state.get("cron_service")
    if not cron_service:
        raise HTTPException(status_code=503, detail="Cron not available (run gateway for cron)")
    try:
        return delete_cron_job_response(cron_service=cron_service, job_id=job_id)
    except Exception as e:
        logger.error(f"Cron delete error: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


@api_router.post("/cron/jobs/{job_id}/run")
async def cron_run_job(job_id: str, force: bool = False):
    """Run a cron job now."""
    cron_service = app_state.get("cron_service")
    if not cron_service:
        raise HTTPException(status_code=503, detail="Cron not available (run gateway for cron)")
    try:
        return await run_cron_job_response(
            cron_service=cron_service,
            job_id=job_id,
            force=force,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cron run error: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


@api_router.get("/sandbox/containers")
async def sandbox_list_containers(browser_only: bool = False):
    """List sandbox containers (browser_only=true for browser containers only)."""
    try:
        return list_sandbox_containers_response(
            load_persistent_state=_load_persistent_state,
            browser_only=browser_only,
        )
    except Exception as e:
        logger.error(f"Sandbox list error: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


@api_router.get("/sandbox/explain")
async def sandbox_explain(session: str = "", agent: str = ""):
    """Get sandbox policy and backend explain (session/agent optional)."""
    try:
        return sandbox_explain_response(
            load_persistent_state=_load_persistent_state,
            session=session,
            agent=agent,
        )
    except Exception as e:
        logger.error(f"Sandbox explain error: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


class SandboxRecreateBody(BaseModel):
    all: bool = False
    session: str | None = None
    agent: str | None = None
    browser_only: bool = False
    force: bool = False


@api_router.post("/sandbox/recreate")
async def sandbox_recreate(body: SandboxRecreateBody):
    """Recreate (remove) sandbox containers by scope."""
    try:
        return sandbox_recreate_response(
            load_persistent_state=_load_persistent_state,
            save_persistent_state=_save_persistent_state,
            all_items=body.all,
            session=body.session,
            agent=body.agent,
            browser_only=body.browser_only,
            force=body.force,
        )
    except Exception as e:
        logger.error(f"Sandbox recreate error: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


@api_router.post("/v1/chat/completions")
async def openai_chat_completions(body: OpenAIChatCompletionsRequest):
    """OpenAI-compatible chat completions (optional agent_id for multi-agent)."""
    agent = resolve_agent_or_503(agent_id=body.agent_id, resolve_agent=_resolve_agent)
    # Use last user message as prompt; if none, concatenate all user messages
    prompt = build_openai_prompt(body.messages)
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="messages must contain at least one user message with content")

    session_key = (body.session_id or "openai:default").strip() or "openai:default"
    id_ = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    if body.stream:
        return build_openai_streaming_response(
            agent=agent,
            prompt=prompt,
            session_key=session_key,
            completion_id=id_,
            model=body.model,
            created=created,
            log_exception=logger.exception,
        )

    return await build_openai_non_streaming_response(
        agent=agent,
        prompt=prompt,
        session_key=session_key,
        completion_id=id_,
        created=created,
        model=body.model,
        log_error=logger.error,
        error_detail=unknown_error_detail,
    )


def _ensure_rpc_rate_limiter(app_state: dict[str, Any]) -> AuthRateLimiter:
    limiter = app_state.get("rpc_auth_rate_limiter")
    if limiter is None:
        limiter = AuthRateLimiter()
        app_state["rpc_auth_rate_limiter"] = limiter
    return limiter


async def _handle_rpc_request(
    req: dict[str, Any],
    client: RpcClientState,
    connection_key: str,
    emit_event: Callable[[str, Any], Awaitable[None]] | None = None,
    client_host: str | None = None,
) -> tuple[bool, Any | None, dict[str, Any] | None]:
    guard_result = prepare_rpc_request_context(
        req=req,
        client=client,
        connection_key=connection_key,
        app_state=app_state,
        rpc_error=lambda code, message, data=None: _rpc_error(code, message, data),
        get_cached_config=get_cached_config,
        node_registry_cls=NodeRegistry,
        is_method_allowed_by_canary=_is_method_allowed_by_canary,
        authorize_rpc_method=_authorize_rpc_method,
        log_denied=lambda method, role, scopes, client_id: logger.info(
            "RPC denied method={} role={} scopes={} client={}",
            method,
            role,
            scopes,
            client_id,
        ),
    )
    if guard_result.error is not None:
        return False, None, guard_result.error

    method = guard_result.method or ""
    params = guard_result.params
    config = guard_result.config
    node_registry: NodeRegistry = guard_result.node_registry or NodeRegistry()

    try:
        rpc_error = make_rpc_error_adapter(_rpc_error)
        broadcast_rpc_event = make_broadcast_rpc_event_adapter(_broadcast_rpc_event)
        connect_logger = make_connect_logger(logger.info)
        browser_control_url = (
            (app_state.get("browser_control_url") or "") or resolve_browser_control_url()
        )

        async def _rpc_chat(msg: ChatMessage) -> dict[str, Any]:
            """Chat callable for RPC: runs agent with on_chat_delta to emit and broadcast chat deltas."""
            agent = resolve_agent_or_503(agent_id=msg.agent_id, resolve_agent=_resolve_agent)
            config = get_cached_config()
            from joyhousebot.services.chat.trace_context import trace_run_id, trace_session_key

            run_id = trace_run_id.get() or ""
            session_key = trace_session_key.get() or ""

            async def on_chat_delta(text: str) -> None:
                payload: dict[str, Any] = {
                    "runId": run_id,
                    "sessionKey": session_key,
                    "state": "delta",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": text}],
                        "timestamp": int(time.time() * 1000),
                    },
                }
                if emit_event:
                    await emit_event("chat", payload)
                await broadcast_rpc_event("chat", payload, None)

            return await build_chat_response(
                agent=agent,
                message=msg.message,
                session_id=msg.session_id,
                log_error=logger.error,
                error_detail=unknown_error_detail,
                config=config,
                check_abort_requested=_check_abort_requested,
                on_chat_delta=on_chat_delta,
            )

        chat = _rpc_chat

        # Lane queue: when chat_session_serialization is True, enqueue busy-session requests and trigger next on complete.
        serialization = (
            config is not None
            and getattr(getattr(config, "gateway", None), "chat_session_serialization", True)
        )
        lane_can_run_ctx = None
        lane_enqueue_ctx = None
        complete_agent_job_ctx = _complete_agent_job
        if serialization:
            from joyhousebot.services.lanes import (
                lane_can_run as _lane_can_run,
                lane_dequeue_next,
                lane_enqueue as _lane_enqueue,
            )
            from joyhousebot.services.chat.chat_service import run_agent_job_with_params

            def _trigger_lane_next(sk: str) -> None:
                next_item = lane_dequeue_next(app_state, sk)
                if not next_item:
                    return
                _register_agent_job(next_item["runId"], next_item["sessionKey"])
                asyncio.create_task(
                    run_agent_job_with_params(
                        item=next_item,
                        chat=chat,
                        chat_message_cls=ChatMessage,
                        complete_agent_job=complete_agent_job_ctx,
                        emit_event=emit_event,
                        fanout_chat_to_subscribed_nodes=_fanout_chat_to_subscribed_nodes,
                        broadcast_rpc_event=broadcast_rpc_event,
                        persist_trace=_persist_trace,
                    )
                )

            def _complete_and_trigger(
                run_id: str, *, status: str = "ok", error: str | None = None, result: dict[str, Any] | None = None
            ) -> None:
                _complete_agent_job(run_id, status=status, error=error, result=result)
                jobs = app_state.get("rpc_agent_jobs") or {}
                sk = (jobs.get(run_id) or {}).get("sessionKey")
                if sk:
                    _trigger_lane_next(sk)

            complete_agent_job_ctx = _complete_and_trigger
            max_pending = getattr(getattr(config, "gateway", None), "max_lane_pending", None) or 100
            lane_can_run_ctx = lambda sk: _lane_can_run(app_state, sk)
            lane_enqueue_ctx = lambda sk, rid, p: _lane_enqueue(
                app_state, sk, rid, p, _now_ms(), max_pending_per_lane=max_pending
            )

        dispatch_context = RpcDispatchContext(
            method=method,
            params=params,
            client=client,
            connection_key=connection_key,
            client_host=client_host,
            config=config,
            app_state=app_state,
            get_connect_nonce=lambda k: (app_state.get("rpc_connect_nonces") or {}).get(k),
            rate_limiter=_ensure_rpc_rate_limiter(app_state),
            node_registry=node_registry,
            emit_event=emit_event,
            rpc_error=rpc_error,
            broadcast_rpc_event=broadcast_rpc_event,
            connect_logger=connect_logger,
            browser_control_url=browser_control_url,
            resolve_agent=_resolve_agent,
            build_sessions_list_payload=_build_sessions_list_payload,
            control_overview=control_overview,
            gateway_methods_with_plugins=_gateway_methods_with_plugins,
            gateway_events=GATEWAY_EVENTS,
            presence_entries=presence_store.list_entries,
            normalize_presence_entry=_normalize_presence_entry,
            build_actions_catalog=_build_actions_catalog,
            now_ms=_now_ms,
            resolve_canvas_host_url=_resolve_canvas_host_url,
            run_rpc_shadow=_run_rpc_shadow,
            build_agents_list_payload=_build_agents_list_payload,
            normalize_agent_id=_normalize_agent_id,
            ensure_agent_workspace_bootstrap=_ensure_agent_workspace_bootstrap,
            save_config=save_config,
            get_cached_config=get_cached_config,
            get_models_payload=_get_models_payload,
            build_auth_profiles_report=build_auth_profiles_report,
            validate_action_candidate=_validate_action_candidate,
            validate_action_batch=_validate_action_batch,
            get_alerts_lifecycle_view=_get_alerts_lifecycle_view,
            get_store=_get_store,
            load_persistent_state=_load_persistent_state,
            run_update_install=_run_update_install,
            create_task=asyncio.create_task,
            register_agent_job=_register_agent_job,
            get_running_run_id_for_session=_get_running_run_id_for_session,
            complete_agent_job=complete_agent_job_ctx,
            wait_agent_job=_wait_agent_job,
            chat=chat,
            chat_message_cls=ChatMessage,
            build_chat_history_payload=_build_chat_history_payload,
            now_iso=lambda: datetime.now().isoformat(),
            fanout_chat_to_subscribed_nodes=_fanout_chat_to_subscribed_nodes,
            lane_can_run=lane_can_run_ctx,
            lane_enqueue=lane_enqueue_ctx,
            persist_trace=_persist_trace,
            apply_session_patch=_apply_session_patch,
            delete_session=delete_session,
            empty_usage_totals=_empty_usage_totals,
            session_usage_entry=_session_usage_entry,
            estimate_tokens=_estimate_tokens,
            build_config_snapshot=_build_config_snapshot,
            build_config_schema_payload=_build_config_schema_payload,
            apply_config_from_raw=_apply_config_from_raw,
            update_config=update_config,
            config_update_cls=ConfigUpdate,
            save_persistent_state=_save_persistent_state,
            build_skills_status_report=_build_skills_status_report,
            build_channels_status_snapshot=_build_channels_status_snapshot,
            load_device_pairs_state=_load_device_pairs_state,
            hash_pairing_token=_hash_pairing_token,
            resolve_node_command_allowlist=_resolve_node_command_allowlist,
            is_node_command_allowed=_is_node_command_allowed,
            normalize_node_event_payload=_normalize_node_event_payload,
            run_node_agent_request=lambda *, node_id, payload_value: _run_node_agent_request(
                node_id=node_id,
                payload_value=payload_value,
                emit_event=emit_event,
            ),
            resolve_browser_node=_resolve_browser_node,
            persist_browser_proxy_files=_persist_browser_proxy_files,
            apply_browser_proxy_paths=_apply_browser_proxy_paths,
            cleanup_expired_exec_approvals=_cleanup_expired_exec_approvals,
            cron_list_jobs=cron_list_jobs,
            cron_add_job=cron_add_job,
            cron_patch_job=cron_patch_job,
            cron_delete_job=cron_delete_job,
            cron_run_job=cron_run_job,
            build_cron_add_body_from_params=build_cron_add_body_from_params,
            cron_job_create_cls=CronJobCreate,
            cron_schedule_body_cls=CronScheduleBody,
            build_cron_patch_body_from_params=build_cron_patch_body_from_params,
            cron_job_patch_cls=CronJobPatch,
            plugin_gateway_methods=_plugin_gateway_methods,
            check_abort_requested=_check_abort_requested,
            request_abort=_request_abort,
        )
        dispatch_handlers = RpcDispatchHandlers(
            try_handle_connect_method=try_handle_connect_method,
            try_handle_health_status_method=try_handle_health_status_method,
            handle_agents_with_shadow=handle_agents_with_shadow,
            try_handle_misc_method=try_handle_misc_method,
            try_handle_chat_runtime_method=try_handle_chat_runtime_method,
            handle_sessions_usage_with_shadow=handle_sessions_usage_with_shadow,
            handle_config_with_shadow=handle_config_with_shadow,
            try_handle_plugins_method=try_handle_plugins_method,
            try_handle_control_state_method=try_handle_control_state_method,
            try_handle_web_login_method=try_handle_web_login_method,
            try_handle_pairing_method=try_handle_pairing_method,
            try_handle_node_runtime_method=try_handle_node_runtime_method,
            try_handle_browser_method=try_handle_browser_method,
            try_handle_exec_approval_method=try_handle_exec_approval_method,
            try_handle_sandbox_method=try_handle_sandbox_method,
            try_handle_cron_method=try_handle_cron_method,
            try_handle_plugin_gateway_method=try_handle_plugin_gateway_method,
            try_handle_lanes_method=try_handle_lanes_method,
            try_handle_traces_method=try_handle_traces_method,
        )
        pipeline_result = await run_handler_pipeline(
            build_rpc_dispatch_handlers_from_context(
                context=dispatch_context,
                handlers=dispatch_handlers,
            )
        )
        if pipeline_result is not None:
            return pipeline_result

        return unknown_method_result(
            method=method,
            rpc_error=rpc_error,
        )
    except HTTPException as e:
        return http_exception_result(
            method=method,
            exc=e,
            log_info=logger.info,
            rpc_error=rpc_error,
        )
    except Exception as e:
        return unhandled_exception_result(
            method=method,
            exc=e,
            log_exception=logger.exception,
            rpc_error=rpc_error,
        )


@app.websocket("/ws/rpc")
async def websocket_rpc(websocket: WebSocket):
    """OpenClaw-compatible Gateway RPC endpoint (req/res/event over WS)."""
    connection_key, client_host, client, emit_event = await bootstrap_rpc_ws_connection(
        websocket=websocket,
        app_state=app_state,
        presence_upsert=presence_store.upsert,
        client_state_cls=RpcClientState,
    )

    try:
        await run_rpc_ws_loop(
            websocket=websocket,
            connection_key=connection_key,
            client_host=client_host,
            client=client,
            app_state=app_state,
            emit_event=emit_event,
            handle_rpc_request=lambda frame, rpc_client, key, emitter, c_host=None: _handle_rpc_request(
                frame,
                rpc_client,
                key,
                emit_event=emitter,
                client_host=c_host,
            ),
            presence_upsert=presence_store.upsert,
            presence_entries=presence_store.list_entries,
            normalize_presence_entry=_normalize_presence_entry,
            rpc_error=lambda code, message, data=None: _rpc_error(code, message, data),
            logger_info=logger.info,
            handle_connect_postprocess=handle_rpc_connect_postprocess,
            node_session_cls=NodeSession,
            node_registry_cls=NodeRegistry,
            now_ms=_now_ms,
        )
    except WebSocketDisconnect:
        await handle_rpc_ws_close(
            connection_key=connection_key,
            app_state=app_state,
            node_registry_cls=NodeRegistry,
            presence_remove_by_connection=presence_store.remove_by_connection,
        )
    except Exception as e:
        await handle_rpc_ws_close(
            connection_key=connection_key,
            app_state=app_state,
            node_registry_cls=NodeRegistry,
            presence_remove_by_connection=presence_store.remove_by_connection,
            logger_error=logger.error,
            exc=e,
        )


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for real-time chat."""
    connection_key = await bootstrap_chat_ws_connection(
        websocket=websocket,
        manager_connect=manager.connect,
        ws_to_presence_key=ws_to_presence_key,
        presence_upsert=presence_store.upsert,
    )

    try:
        await run_chat_ws_loop(
            websocket=websocket,
            connection_key=connection_key,
            presence_upsert=presence_store.upsert,
            resolve_agent=_resolve_agent,
            logger_error=logger.error,
        )
    except WebSocketDisconnect:
        handle_chat_ws_close(
            websocket=websocket,
            ws_to_presence_key=ws_to_presence_key,
            presence_remove_by_connection=presence_store.remove_by_connection,
            manager_disconnect=manager.disconnect,
        )
    except Exception as e:
        handle_chat_ws_close(
            websocket=websocket,
            ws_to_presence_key=ws_to_presence_key,
            presence_remove_by_connection=presence_store.remove_by_connection,
            manager_disconnect=manager.disconnect,
            logger_error=logger.error,
            exc=e,
        )


@app.websocket("/ws/agent-stream")
async def websocket_agent_stream(websocket: WebSocket):
    """WebSocket for programming workspace: stream LLM deltas, tool execution, and real-time tool output (e.g. code_runner stdout/stderr)."""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") != "message":
                continue
            message = (data.get("message") or "").strip()
            session_id = (data.get("session_id") or "ui:workspace").strip()
            agent_id = (data.get("agent_id") or "default").strip() or None
            if not message:
                await websocket.send_json({"type": "error", "error": "message is required"})
                continue

            agent = _resolve_agent(agent_id)
            if not agent:
                await websocket.send_json({"type": "error", "error": "Agent not available"})
                continue

            async def send_event(etype: str, payload: dict) -> None:
                await websocket.send_json({"type": "event", "event": etype, "payload": payload})

            try:
                await agent.process_direct(
                    content=message,
                    session_key=session_id,
                    channel="api",
                    chat_id="agent-stream",
                    execution_stream_callback=send_event,
                )
            except Exception as e:
                logger.exception("Agent stream error: %s", e)
                await websocket.send_json({"type": "error", "error": str(e)})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("WebSocket agent-stream error: %s", e)


@api_router.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Transcribe audio file to text."""
    transcription = app_state.get("transcription_provider")
    if not transcription:
        raise HTTPException(
            status_code=503,
            detail="Transcription provider not configured. Add Groq API key to config."
        )
    
    try:
        text = await transcribe_upload_file(
            file=file,
            transcription_provider=transcription,
            timestamp=int(time.time()),
        )
        return {
            "ok": True,
            "text": text
        }
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


@api_router.get("/tasks")
async def list_tasks(status: str | None = None, limit: int = 50):
    """List tasks from local storage."""
    try:
        store = LocalStateStore.default()
        return list_tasks_response(store=store, status=status, limit=limit)
    except Exception as e:
        logger.error(f"Failed to list tasks: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


@api_router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get task details."""
    try:
        store = LocalStateStore.default()
        return get_task_response(store=store, task_id=task_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get task: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


@api_router.get("/tasks/{task_id}/events")
async def list_task_events(task_id: str, limit: int = 100):
    """List execution events for a task (for GUI)."""
    try:
        store = LocalStateStore.default()
        return list_task_events_response(store=store, task_id=task_id, limit=limit)
    except Exception as e:
        logger.error(f"Failed to list task events: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


@api_router.get("/identity")
async def get_identity():
    """Get bot identity information."""
    try:
        store = LocalStateStore.default()
        return get_identity_response(store=store)
    except Exception as e:
        logger.error(f"Failed to get identity: {e}")
        raise HTTPException(status_code=500, detail=unknown_error_detail(e))


# 必须在所有 api_router 路由定义之后注册，否则后面的路由不会生效
app.include_router(api_router, prefix="/api")


def create_app() -> FastAPI:
    """Create and return the FastAPI application."""
    return app


def run_server(host: str = "127.0.0.1", port: int = 8765):
    """Run the API server."""
    uvicorn.run(
        app,
        host=host,
        port=port,
        timeout_keep_alive=30,
        timeout_graceful_shutdown=10,
        limit_concurrency=None,
        log_level="warning",
    )
