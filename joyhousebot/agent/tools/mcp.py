"""MCP client: connects to MCP servers and wraps their tools as native joyhousebot tools."""

import asyncio
import re
from contextlib import AsyncExitStack
from typing import Any

import httpx
from loguru import logger

from joyhousebot.agent.tools.base import Tool
from joyhousebot.capabilities.registry import CapabilityRegistry
from joyhousebot.capabilities.tool_adapter import ToolInvocationError
from joyhousebot.utils.exceptions import (
    classify_exception,
    sanitize_error_message,
)
from joyhousebot.utils.ssrf import SsrfProtectedTransport, validate_url_with_dns

_MCP_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")

_MCP_TOOL_TIMEOUT = 60.0
# Long read timeout: streamable HTTP MCP servers hold SSE streams open.
_MCP_HTTP_TIMEOUT = httpx.Timeout(30.0, read=300.0)


def _sanitize_mcp_tool_name(server_name: str, raw_name: str) -> str:
    """Make MCP tool name provider-safe (no dots, etc)."""
    safe_server = re.sub(r"[^a-zA-Z0-9_-]", "_", str(server_name)).strip("_") or "mcp"
    safe_tool = re.sub(r"[^a-zA-Z0-9_-]", "_", str(raw_name)).strip("_") or "tool"
    base = f"mcp_{safe_server}_{safe_tool}"
    return base if _MCP_SAFE_NAME.match(base) else base


class MCPToolWrapper(Tool):
    """Wraps a single MCP server tool as a joyhousebot Tool."""

    # MCP metadata does not yet expose a trusted immutable side-effect
    # contract, so unknown tools fail closed into the approval path.
    side_effect = "unknown"
    idempotent = False

    def __init__(self, session, server_name: str, tool_def, timeout: float = _MCP_TOOL_TIMEOUT):
        self._session = session
        self._server_name = server_name
        self._original_name = tool_def.name
        self._name = _sanitize_mcp_tool_name(server_name, tool_def.name)
        # Descriptions come from an external MCP server and are untrusted content.
        self._description = (
            f"[Untrusted MCP tool from server '{server_name}'] "
            f"{tool_def.description or tool_def.name}"
        )
        self._parameters = tool_def.inputSchema or {"type": "object", "properties": {}}
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
            parts = []
            for block in result.content:
                if isinstance(block, types.TextContent):
                    parts.append(block.text)
                else:
                    parts.append(str(block))
            output = "\n".join(parts) or "(no output)"
            # MCP output is untrusted content; wrap it in explicit boundary
            # markers so it cannot be confused with system instructions.
            return (
                f'<mcp_tool_result server="{self._server_name}" name="{self._original_name}">\n'
                f"{output}\n"
                f"</mcp_tool_result>"
            )
        except asyncio.TimeoutError:
            raise ToolInvocationError(
                "MCP_TIMEOUT", f"MCP tool call timed out after {self._timeout}s", retryable=True
            )
        except ConnectionError as e:
            raise ToolInvocationError(
                "MCP_CONNECTION_FAILED", sanitize_error_message(str(e)), retryable=True
            ) from e
        except Exception as e:
            code, _, _ = classify_exception(e)
            sanitized = sanitize_error_message(str(e))
            logger.warning(f"MCP tool '{self.name}' error [{code}]: {sanitized}")
            raise ToolInvocationError("MCP_CALL_FAILED", sanitized) from e


async def connect_mcp_servers(
    mcp_servers: dict,
    registry: CapabilityRegistry,
    stack: AsyncExitStack,
    tool_timeout: float = _MCP_TOOL_TIMEOUT,
) -> None:
    """Connect to configured MCP servers and register their tools.

    HTTP MCP servers (`cfg.url`) go through the same SSRF egress validation as
    the web tools before any connection is made, and every underlying HTTP
    connection is pinned to the validated DNS answer via SsrfProtectedTransport.
    Limitation: validation happens when the (long-lived) connection is
    established; if a reconnect happens later the transport re-validates DNS,
    but the session itself is not re-authorized mid-stream.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    for name, cfg in mcp_servers.items():
        try:
            if not getattr(cfg, "enabled", True):
                logger.info(f"MCP server '{name}': disabled, skipping")
                continue
            if cfg.command:
                params = StdioServerParameters(
                    command=cfg.command, args=cfg.args, env=cfg.env or None
                )
                read, write = await stack.enter_async_context(stdio_client(params))
            elif cfg.url:
                from mcp.client.streamable_http import streamable_http_client

                ok, err = await validate_url_with_dns(cfg.url)
                if not ok:
                    logger.error(
                        f"MCP server '{name}': URL blocked - {sanitize_error_message(err)}"
                    )
                    continue
                http_client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        transport=SsrfProtectedTransport(),
                        follow_redirects=True,
                        timeout=_MCP_HTTP_TIMEOUT,
                    )
                )
                read, write, _ = await stack.enter_async_context(
                    streamable_http_client(cfg.url, http_client=http_client)
                )
            else:
                logger.warning(f"MCP server '{name}': no command or url configured, skipping")
                continue

            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            tools = await session.list_tools()
            for tool_def in tools.tools:
                wrapper = MCPToolWrapper(session, name, tool_def, timeout=tool_timeout)
                # External MCP tools are never enabled implicitly.  Operators
                # must explicitly allowlist the published capability names.
                registry.register_tool(wrapper, optional=True)
                logger.debug(f"MCP: registered tool '{wrapper.name}' from server '{name}'")

            logger.info(f"MCP server '{name}': connected, {len(tools.tools)} tools registered")
        except FileNotFoundError as e:
            logger.error(
                f"MCP server '{name}': command not found - {sanitize_error_message(str(e))}"
            )
        except asyncio.TimeoutError:
            logger.error(f"MCP server '{name}': connection timed out")
        except ConnectionError as e:
            logger.error(
                f"MCP server '{name}': connection failed - {sanitize_error_message(str(e))}"
            )
        except PermissionError as e:
            logger.error(
                f"MCP server '{name}': permission denied - {sanitize_error_message(str(e))}"
            )
        except Exception as e:
            code, category, _ = classify_exception(e)
            logger.error(
                f"MCP server '{name}': failed to connect [{code}] - {sanitize_error_message(str(e))}"
            )
