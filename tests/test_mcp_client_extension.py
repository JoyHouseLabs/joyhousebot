from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest
from porthouse_connector_mcp_client import MCPToolWrapper, create_extension
from porthouse_connector_mcp_client import connector as mcp_connector

from porthouse.capabilities import CapabilityRegistry
from porthouse.connectors import ToolConnectorRegistry


def _tool_def(name: str = "read.file"):
    return SimpleNamespace(
        name=name,
        description="remote tool",
        inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
    )


def test_mcp_connector_has_independent_manifest_and_entry_contract():
    extension = create_extension()
    assert extension.manifest.extension_id == "connector-mcp-client"
    assert extension.manifest.extension_types == ("tool_connector",)
    assert extension.manifest.build_digest.startswith("sha256:")


def test_remote_tool_definition_is_not_owned_by_core():
    wrapper = MCPToolWrapper(SimpleNamespace(), "files", _tool_def())
    definition = mcp_connector._definition("files", _tool_def(), wrapper)
    assert definition.ref.kind.value == "connector"
    assert definition.ref.plugin_id == "connector-mcp-client"
    assert definition.ref.plugin_build_digest == create_extension().manifest.build_digest
    assert definition.side_effect == "unknown"
    assert definition.connection_ids == ("files",)


@pytest.mark.asyncio
async def test_stdio_is_fail_closed_without_explicit_opt_in(monkeypatch):
    called = False

    def forbidden_stdio(_params):
        nonlocal called
        called = True
        raise AssertionError("stdio must not start")

    monkeypatch.setattr("mcp.client.stdio.stdio_client", forbidden_stdio)
    registry = CapabilityRegistry(optional_allowlist=["mcp_local_read_file"])
    async with AsyncExitStack() as stack:
        await mcp_connector.connect_mcp_servers(
            {
                "local": {
                    "enabled": True,
                    "command": "unsafe-host-command",
                    "args": [],
                    "env": {},
                    "url": "",
                }
            },
            registry,
            stack,
        )
    assert called is False
    assert registry.tool_names == []


@pytest.mark.asyncio
async def test_generic_registry_connects_configured_extension():
    called = []

    async def connect(request):
        called.append(request.settings)

    declared = create_extension()
    extension = type(declared)(manifest=declared.manifest, connect=connect)
    connectors = ToolConnectorRegistry()
    connectors.register(extension, source="test")
    async with AsyncExitStack() as stack:
        await connectors.connect_configured(
            {"connector-mcp-client": {"servers": {}}},
            capability_registry=CapabilityRegistry(),
            lifecycle=stack,
        )
    assert called == [{"servers": {}}]
