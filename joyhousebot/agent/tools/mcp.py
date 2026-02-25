"""MCP client: connects to MCP servers and wraps their tools as native joyhousebot tools."""

import asyncio
import re
from contextlib import AsyncExitStack
from typing import Any

from loguru import logger

from joyhousebot.agent.tools.base import Tool
from joyhousebot.agent.tools.registry import ToolRegistry
from joyhousebot.utils.exceptions import (
    ToolError,
    PluginError,
    sanitize_error_message,
    classify_exception,
)

_MCP_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")


def _sanitize_mcp_tool_name(server_name: str, raw_name: str) -> str:
    """Make MCP tool name provider-safe (no dots, etc)."""
    safe_server = re.sub(r"[^a-zA-Z0-9_-]", "_", str(server_name)).strip("_") or "mcp"
    safe_tool = re.sub(r"[^a-zA-Z0-9_-]", "_", str(raw_name)).strip("_") or "tool"
    base = f"mcp_{safe_server}_{safe_tool}"
    return base if _MCP_SAFE_NAME.match(base) else base


class MCPToolWrapper(Tool):
    """Wraps a single MCP server tool as a joyhousebot Tool."""

    def __init__(self, session, server_name: str, tool_def):
        self._session = session
        self._original_name = tool_def.name
        self._name = _sanitize_mcp_tool_name(server_name, tool_def.name)
        self._description = tool_def.description or tool_def.name
        self._parameters = tool_def.inputSchema or {"type": "object", "properties": {}}

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
            result = await self._session.call_tool(self._original_name, arguments=kwargs)
            parts = []
            for block in result.content:
                if isinstance(block, types.TextContent):
                    parts.append(block.text)
                else:
                    parts.append(str(block))
            return "\n".join(parts) or "(no output)"
        except asyncio.TimeoutError:
            raise ToolError(self.name, "MCP tool call timed out")
        except ConnectionError as e:
            raise ToolError(self.name, f"MCP connection error: {sanitize_error_message(str(e))}")
        except Exception as e:
            code, _, _ = classify_exception(e)
            sanitized = sanitize_error_message(str(e))
            logger.warning(f"MCP tool '{self.name}' error [{code}]: {sanitized}")
            return f"Error: MCP tool '{self._original_name}' failed - {sanitized}"


async def connect_mcp_servers(
    mcp_servers: dict, registry: ToolRegistry, stack: AsyncExitStack
) -> None:
    """Connect to configured MCP servers and register their tools."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    for name, cfg in mcp_servers.items():
        try:
            if cfg.command:
                params = StdioServerParameters(
                    command=cfg.command, args=cfg.args, env=cfg.env or None
                )
                read, write = await stack.enter_async_context(stdio_client(params))
            elif cfg.url:
                from mcp.client.streamable_http import streamable_http_client
                read, write, _ = await stack.enter_async_context(
                    streamable_http_client(cfg.url)
                )
            else:
                logger.warning(f"MCP server '{name}': no command or url configured, skipping")
                continue

            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            tools = await session.list_tools()
            for tool_def in tools.tools:
                wrapper = MCPToolWrapper(session, name, tool_def)
                registry.register(wrapper)
                logger.debug(f"MCP: registered tool '{wrapper.name}' from server '{name}'")

            logger.info(f"MCP server '{name}': connected, {len(tools.tools)} tools registered")
        except FileNotFoundError as e:
            logger.error(f"MCP server '{name}': command not found - {sanitize_error_message(str(e))}")
        except asyncio.TimeoutError:
            logger.error(f"MCP server '{name}': connection timed out")
        except ConnectionError as e:
            logger.error(f"MCP server '{name}': connection failed - {sanitize_error_message(str(e))}")
        except PermissionError as e:
            logger.error(f"MCP server '{name}': permission denied - {sanitize_error_message(str(e))}")
        except Exception as e:
            code, category, _ = classify_exception(e)
            logger.error(f"MCP server '{name}': failed to connect [{code}] - {sanitize_error_message(str(e))}")
