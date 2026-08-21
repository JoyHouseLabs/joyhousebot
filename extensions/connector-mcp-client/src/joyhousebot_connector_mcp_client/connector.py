"""Connect external MCP servers and expose their Tools through Core governance."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any

import httpx

from joyhousebot.extension_sdk import (
    CapabilityConnectorConnectRequest,
    CapabilityConnectorExtension,
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
    ExtensionManifest,
)
from joyhousebot.extension_sdk.connectors import connector_settings
from joyhousebot.extension_sdk.manifest import source_tree_digest
from joyhousebot.extension_sdk.network import (
    SsrfProtectedTransport,
    sanitize_error_message,
    validate_url_with_dns,
)
from joyhousebot.extension_sdk.tools import Tool, ToolInvocationError

logger = logging.getLogger(__name__)

_MCP_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")
_MCP_TOOL_TIMEOUT = 60.0
_MCP_HTTP_TIMEOUT = httpx.Timeout(30.0, read=300.0)
_BUILD_DIGEST = source_tree_digest(__file__)

MCP_CLIENT_MANIFEST = ExtensionManifest(
    extension_id="connector-mcp-client",
    version="0.1.0",
    name="joyhousebot MCP Client Connector",
    extension_types=("connector",),
    description="Connect explicitly configured MCP servers as governed optional Tools.",
    distribution_name="joyhousebot-connector-mcp-client",
    build_digest=_BUILD_DIGEST,
    execution_isolation="mcp",
    required_permissions=("connector.mcp.invoke",),
    dependencies=(
        {"id": "mcp-server", "kind": "service", "required": True},
    ),
    configuration_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "allow_stdio": {"type": "boolean", "default": False},
            "tool_timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "maximum": 3600,
            },
            "servers": {"type": "object", "additionalProperties": {"type": "object"}},
        },
    },
)


def _sanitize_mcp_tool_name(server_name: str, raw_name: str) -> str:
    safe_server = re.sub(r"[^a-zA-Z0-9_-]", "_", str(server_name)).strip("_") or "mcp"
    safe_tool = re.sub(r"[^a-zA-Z0-9_-]", "_", str(raw_name)).strip("_") or "tool"
    value = f"mcp_{safe_server}_{safe_tool}"
    return value if _MCP_SAFE_NAME.match(value) else "mcp_tool"


def _tool_schema(tool_def: Any) -> dict[str, Any]:
    value = getattr(tool_def, "inputSchema", None)
    if not isinstance(value, dict) or value.get("type", "object") != "object":
        return {"type": "object", "properties": {}}
    return dict(value)


def _tool_version(server_name: str, tool_def: Any) -> str:
    material = repr(
        (
            str(server_name),
            str(getattr(tool_def, "name", "")),
            _tool_schema(tool_def),
        )
    ).encode()
    return f"1.0.0+schema.{hashlib.sha256(material).hexdigest()[:12]}"


class MCPToolWrapper(Tool):
    """Wrap one untrusted remote MCP Tool behind explicit content boundaries."""

    side_effect = "unknown"
    idempotent = False
    retryable = True
    data_classification = "confidential"

    def __init__(
        self,
        session: Any,
        server_name: str,
        tool_def: Any,
        timeout: float = _MCP_TOOL_TIMEOUT,
    ) -> None:
        self._session = session
        self._server_name = server_name
        self._original_name = str(tool_def.name)
        self._name = _sanitize_mcp_tool_name(server_name, self._original_name)
        self._description = (
            f"[Untrusted MCP tool from server '{server_name}'] "
            f"{getattr(tool_def, 'description', None) or self._original_name}"
        )
        self._parameters = _tool_schema(tool_def)
        self._timeout = timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        from mcp import types

        try:
            result = await asyncio.wait_for(
                self._session.call_tool(self._original_name, arguments=kwargs),
                timeout=self._timeout,
            )
            parts = [
                block.text if isinstance(block, types.TextContent) else str(block)
                for block in result.content
            ]
            output = "\n".join(parts) or "(no output)"
            return (
                f'<mcp_tool_result server="{self._server_name}" '
                f'name="{self._original_name}">\n{output}\n</mcp_tool_result>'
            )
        except asyncio.TimeoutError:
            raise ToolInvocationError(
                "MCP_TIMEOUT",
                f"MCP tool call timed out after {self._timeout}s",
                retryable=True,
            )
        except ConnectionError as exc:
            raise ToolInvocationError(
                "MCP_CONNECTION_FAILED",
                sanitize_error_message(str(exc)),
                retryable=True,
            ) from exc
        except ToolInvocationError:
            raise
        except Exception as exc:
            message = sanitize_error_message(str(exc))
            logger.warning("MCP Tool %s failed: %s", self.name, message)
            raise ToolInvocationError("MCP_CALL_FAILED", message) from exc


def _definition(server_name: str, tool_def: Any, wrapper: MCPToolWrapper) -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=CapabilityRef(
            wrapper.name,
            _tool_version(server_name, tool_def),
            CapabilityKind.CONNECTOR,
            MCP_CLIENT_MANIFEST.extension_id,
            MCP_CLIENT_MANIFEST.version,
            MCP_CLIENT_MANIFEST.build_digest,
        ),
        name=wrapper.name,
        description=wrapper.description,
        input_schema=wrapper.parameters,
        output_schema={"type": "object"},
        adapter="connector:mcp",
        tags=("mcp", "external", "untrusted"),
        timeout_seconds=max(1, int(wrapper._timeout)),
        idempotent=False,
        retryable=True,
        side_effect="unknown",
        invocation_concurrency="sequential",
        max_concurrent_invocations=1,
        data_classification="confidential",
        connection_ids=(server_name,),
        origin={
            "extension_id": MCP_CLIENT_MANIFEST.extension_id,
            "extension_version": MCP_CLIENT_MANIFEST.version,
            "extension_build_digest": MCP_CLIENT_MANIFEST.build_digest,
        },
    )


async def connect_mcp_servers(
    servers: dict[str, Any],
    registry: Any,
    lifecycle: Any,
    *,
    tool_timeout: float = _MCP_TOOL_TIMEOUT,
    allow_stdio: bool = False,
) -> None:
    """Connect configured servers and register versioned optional capabilities."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    for name, raw_config in servers.items():
        try:
            config = connector_settings(raw_config)
            if not bool(config.get("enabled", True)):
                continue
            command = str(config.get("command") or "").strip()
            url = str(config.get("url") or "").strip()
            if command:
                if not allow_stdio:
                    logger.error(
                        "MCP server %r uses stdio but allow_stdio is false; skipping", name
                    )
                    continue
                params = StdioServerParameters(
                    command=command,
                    args=[str(item) for item in config.get("args") or ()],
                    env={str(key): str(value) for key, value in (config.get("env") or {}).items()}
                    or None,
                )
                read, write = await lifecycle.enter_async_context(stdio_client(params))
            elif url:
                from mcp.client.streamable_http import streamable_http_client

                ok, error = await validate_url_with_dns(url)
                if not ok:
                    logger.error("MCP server %r URL blocked: %s", name, sanitize_error_message(error))
                    continue
                client = await lifecycle.enter_async_context(
                    httpx.AsyncClient(
                        transport=SsrfProtectedTransport(),
                        follow_redirects=True,
                        timeout=_MCP_HTTP_TIMEOUT,
                    )
                )
                read, write, _ = await lifecycle.enter_async_context(
                    streamable_http_client(url, http_client=client)
                )
            else:
                logger.warning("MCP server %r has neither command nor url", name)
                continue

            session = await lifecycle.enter_async_context(ClientSession(read, write))
            await session.initialize()
            catalog = await session.list_tools()
            for tool_def in catalog.tools:
                wrapper = MCPToolWrapper(session, str(name), tool_def, timeout=tool_timeout)
                registry.register_connector_capability(
                    wrapper,
                    optional=True,
                    definition=_definition(str(name), tool_def, wrapper),
                )
            logger.info("MCP server %r connected with %d Tools", name, len(catalog.tools))
        except (FileNotFoundError, PermissionError, ConnectionError) as exc:
            logger.error("MCP server %r connection failed: %s", name, sanitize_error_message(str(exc)))
        except asyncio.TimeoutError:
            logger.error("MCP server %r connection timed out", name)
        except Exception as exc:
            logger.error("MCP server %r failed: %s", name, sanitize_error_message(str(exc)))


async def _connect(request: CapabilityConnectorConnectRequest) -> None:
    settings = dict(request.settings)
    servers = settings.get("servers") or {}
    if not isinstance(servers, dict):
        raise TypeError("connector-mcp-client settings.servers must be an object")
    timeout = float(settings.get("tool_timeout_seconds") or _MCP_TOOL_TIMEOUT)
    if not 1 <= timeout <= 3600:
        raise ValueError("tool_timeout_seconds must be between 1 and 3600")
    await connect_mcp_servers(
        servers,
        request.registry,
        request.lifecycle,
        tool_timeout=timeout,
        allow_stdio=bool(settings.get("allow_stdio", False)),
    )


MCP_CLIENT_EXTENSION = CapabilityConnectorExtension(
    manifest=MCP_CLIENT_MANIFEST,
    connect=_connect,
)


def create_extension() -> CapabilityConnectorExtension:
    return MCP_CLIENT_EXTENSION
