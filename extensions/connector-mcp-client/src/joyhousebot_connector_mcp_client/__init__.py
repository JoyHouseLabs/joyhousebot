"""Optional external MCP client Capability Connector."""

from .connector import (
    MCP_CLIENT_EXTENSION,
    MCPToolWrapper,
    connect_mcp_servers,
    create_extension,
)

__all__ = [
    "MCP_CLIENT_EXTENSION",
    "MCPToolWrapper",
    "connect_mcp_servers",
    "create_extension",
]
