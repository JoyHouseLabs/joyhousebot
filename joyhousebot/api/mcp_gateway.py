"""MCP protocol gateway backed by the unified Run/Task runtime.

The gateway deliberately does not execute business handlers directly.  An MCP
``tools/call`` becomes a one-node durable graph task, so authentication,
leases, retries, events, trace data and result ownership are identical to
HTTP and Web UI executions.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import time
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from joyhousebot.api.dependencies import _bearer_token
from joyhousebot.application.context import Principal, RequestContext
from joyhousebot.application.runs import GraphTaskCommand
from joyhousebot.domain.capabilities.models import CapabilityRef
from joyhousebot.utils.permissions import permission_granted

_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


def _mcp_name(capability_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]+", "_", capability_id).strip("_")
    return f"joy_{value}" if value else "joy_capability"


def _signature_for_schema(schema: dict[str, Any]) -> inspect.Signature:
    """Make FastMCP validate direct tool arguments using a JSON Schema."""
    properties = dict(schema.get("properties") or {})
    required = set(schema.get("required") or [])
    parameters: list[inspect.Parameter] = []
    for name in properties:
        if not _SAFE_NAME.match(str(name)):
            # Dotted or otherwise non-Python fields remain usable through the
            # standard MCP envelope in a future compatibility adapter.
            continue
        default = inspect.Parameter.empty if name in required else None
        parameters.append(
            inspect.Parameter(
                str(name), inspect.Parameter.KEYWORD_ONLY, annotation=Any, default=default
            )
        )
    return inspect.Signature(parameters, return_annotation=dict[str, Any])


class MCPGateway:
    """Expose published executable capabilities as authenticated MCP tools."""

    def __init__(self) -> None:
        self.server = FastMCP(
            "Joyhousebot Capability Gateway",
            instructions=(
                "Published Joyhousebot capabilities. Every call creates a durable Run and Task. "
                "Results are user-scoped and may be asynchronous."
            ),
            streamable_http_path="/",
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(
                allowed_hosts=[
                    "127.0.0.1:*",
                    "localhost:*",
                    "dinq.smartjob.top",
                    "dinq.smartjob.top:*",
                ],
                allowed_origins=["https://dinq.smartjob.top"],
            ),
        )
        self.asgi_app = self.server.streamable_http_app()
        self._container: Any | None = None
        self._tools: dict[str, dict[str, Any]] = {}

    async def configure(self, container: Any) -> None:
        self._container = container
        # A TestClient or an embedded host may restart the parent lifespan;
        # rebuild the dynamic catalog against the new store instead of keeping
        # stale capability definitions from the previous container.
        self._tools.clear()
        self.server._tool_manager._tools.clear()
        definitions = await asyncio.to_thread(container.store.list_capability_definitions)
        for definition in definitions:
            ref = dict(definition.get("ref") or {})
            kind = str(ref.get("kind") or "")
            capability_id = str(ref.get("capability_id") or "").strip()
            if kind not in {"tool", "connector"} or not capability_id:
                continue
            settings = await asyncio.to_thread(
                container.store.get_capability_runtime_settings, capability_id
            )
            if not bool((settings or {}).get("enabled", True)):
                continue
            name = _mcp_name(capability_id)
            if name in self._tools:
                continue
            self._tools[name] = definition
            self._register_tool(name, definition)

    def _register_tool(self, name: str, definition: dict[str, Any]) -> None:
        capability_id = str(dict(definition.get("ref") or {}).get("capability_id") or "")
        description = str(definition.get("description") or capability_id)

        async def invoke(ctx: Context, **kwargs: Any) -> dict[str, Any]:
            return await self._invoke(ctx, capability_id, kwargs)

        invoke.__name__ = name
        invoke.__signature__ = _signature_for_schema(
            dict(definition.get("input_schema") or {"type": "object"})
        )
        self.server.add_tool(
            invoke,
            name=name,
            description=f"{description} (durable Joyhousebot Run)",
            structured_output=True,
        )
        # FastMCP derives validation models from the Python signature.  Keep
        # the published platform schema as the canonical MCP schema as well.
        tool = self.server._tool_manager._tools[name]
        tool.parameters = dict(definition.get("input_schema") or {"type": "object"})

    @staticmethod
    def _request(ctx: Context) -> Any | None:
        try:
            return getattr(ctx.request_context, "request", None)
        except (AttributeError, ValueError):
            return None

    @staticmethod
    def _client_id(ctx: Context) -> str | None:
        try:
            return ctx.client_id
        except (AttributeError, ValueError):
            return None

    async def _principal(self, ctx: Context) -> Principal:
        container = self._container
        if container is None:
            raise HTTPException(status_code=503, detail="MCP gateway is not ready")
        request = self._request(ctx)
        headers = getattr(request, "headers", {}) or {}
        token = _bearer_token(headers.get("authorization"))
        if token:
            access = await asyncio.to_thread(
                container.store.authenticate_api_access_token, token
            )
            if access is not None:
                scopes = tuple(str(item) for item in access.get("scopes") or ())
                if not any(permission_granted(scope, "mcp.invoke") for scope in scopes):
                    raise HTTPException(status_code=403, detail="API token scope required: mcp.invoke")
                user_id = str(access["user_id"])
                admin = await asyncio.to_thread(container.store.get_platform_admin, user_id)
                if admin is not None and admin.enabled:
                    return Principal(
                        subject=f"mcp-token:{access['token_id']}",
                        user_id=user_id,
                        role=admin.role,
                        permissions=tuple(admin.permissions),
                        token_scopes=scopes,
                        token_type=str(access.get("token_type") or "user"),
                    )
                return Principal(
                    subject=f"mcp-token:{access['token_id']}",
                    user_id=user_id,
                    token_scopes=scopes,
                    token_type=str(access.get("token_type") or "user"),
                )
        if bool(getattr(container.config.gateway, "allow_insecure_auth", False)):
            user_id = str(
                headers.get("x-user-id")
                or os.getenv("JOYHOUSEBOT_DEV_USER_ID")
                or "local-dev"
            ).strip()
            admin = await asyncio.to_thread(container.store.get_platform_admin, user_id)
            if admin is not None and admin.enabled:
                return Principal(
                    subject=f"mcp-dev:{user_id}",
                    user_id=user_id,
                    role=admin.role,
                    permissions=tuple(admin.permissions),
                )
            return Principal(subject=f"mcp-dev:{user_id}", user_id=user_id)
        raise HTTPException(status_code=401, detail="invalid or missing MCP bearer token")

    async def _invoke(
        self, ctx: Context, capability_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        container = self._container
        if container is None:
            raise HTTPException(status_code=503, detail="MCP gateway is not ready")
        principal = await self._principal(ctx)
        definition = next(
            (item for item in self._tools.values()
             if str(dict(item.get("ref") or {}).get("capability_id") or "") == capability_id),
            None,
        )
        if definition is None:
            raise HTTPException(status_code=404, detail=f"capability not found: {capability_id}")
        permissions = [str(item) for item in definition.get("permissions") or []]
        # AND semantics, same as the capability dispatcher: every declared
        # permission must be granted. The dispatcher re-checks at execution
        # time, so this is the first of two enforcement layers.
        if permissions and not all(principal.can(item) for item in permissions):
            raise HTTPException(status_code=403, detail="capability permission denied")
        request = self._request(ctx)
        headers = getattr(request, "headers", {}) or {}
        agent_profile = await asyncio.to_thread(container.store.get_agent_profile)
        agent_id = str(headers.get("x-agent-id") or getattr(agent_profile.definition, "agent_id", "default"))
        client_id = self._client_id(ctx)
        session_id = str(headers.get("x-session-id") or f"mcp:{client_id or principal.user_id}")
        request_id = str(headers.get("x-request-id") or f"mcp_{uuid4().hex}")
        timeout_seconds = min(
            300.0,
            max(1.0, float(definition.get("timeout_seconds") or 60)),
        )
        task_id = f"mcp_{uuid4().hex[:16]}"
        record = await container.runs.create_graph(
            RequestContext(
                principal=principal,
                request_id=request_id,
                idempotency_key=headers.get("idempotency-key"),
            ),
            goal=f"MCP invoke {capability_id}",
            agent_id=agent_id,
            session_id=session_id,
            max_concurrent=1,
            fail_fast=True,
            tasks=[
                GraphTaskCommand(
                    id=task_id,
                    name=capability_id,
                    prompt=f"Invoke {capability_id} through the registered capability adapter.",
                    timeout_seconds=timeout_seconds,
                    capability=CapabilityRef.from_dict(dict(definition["ref"])),
                    capability_input=dict(arguments),
                    allowed_tools=[capability_id],
                    metadata={
                        "transport": "mcp",
                        "mcp_client_id": client_id,
                    },
                )
            ],
        )
        if self._request(ctx) is not None:
            await ctx.info(f"Accepted {capability_id} as Run {record.run_id}")
        started = time.monotonic()
        wait_seconds = min(timeout_seconds, 30.0)
        final = await container.runtime.wait(record.run_id, timeout=wait_seconds)
        if final is None or final.status not in _TERMINAL:
            if self._request(ctx) is not None:
                await ctx.report_progress(wait_seconds, timeout_seconds)
            return {
                "run_id": record.run_id,
                "task_id": task_id,
                "status": "accepted",
                "poll": f"/v1/runs/{record.run_id}",
                "events": f"/v1/runs/{record.run_id}/events",
            }
        result = dict(final.result or {})
        return {
            "run_id": final.run_id,
            "task_id": task_id,
            "status": final.status,
            "summary": final.status_summary or result.get("summary") or "",
            "result": result,
            "error": final.error,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }

    async def close(self) -> None:
        self._container = None
