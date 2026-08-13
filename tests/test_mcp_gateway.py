from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from joyhousebot.api.mcp_gateway import MCPGateway


class _Store:
    def list_capability_definitions(self):
        return [
            {
                "ref": {"capability_id": "catalog.item.filter", "version": "1", "kind": "tool", "plugin_id": "sample-catalog", "plugin_version": "1", "plugin_build_digest": "sha256:test"},
                "name": "Catalog filter",
                "description": "Filter catalog items",
                "input_schema": {
                    "type": "object",
                    "properties": {"keyword": {"type": "string"}},
                    "required": ["keyword"],
                },
                "permissions": [],
                "timeout_seconds": 20,
            },
            {
                "ref": {"capability_id": "skill.internal", "version": "1", "kind": "skill", "plugin_id": "sample-catalog", "plugin_version": "1", "plugin_build_digest": "sha256:test"},
                "name": "Internal skill",
                "description": "Not an MCP tool",
                "input_schema": {"type": "object"},
            },
        ]

    def get_capability_runtime_settings(self, _capability_id):
        return {"enabled": True}

    def authenticate_api_access_token(self, _token):
        return None

    def get_platform_admin(self, _user_id):
        return SimpleNamespace(enabled=True, role="admin", permissions=["*"])

    def get_agent_profile(self):
        return SimpleNamespace(definition=SimpleNamespace(agent_id="main-coordinator"))


class _Runs:
    async def create_graph(self, *_args, **_kwargs):
        return SimpleNamespace(run_id="run_mcp_test")


class _Runtime:
    async def wait(self, *_args, **_kwargs):
        return SimpleNamespace(
            run_id="run_mcp_test",
            status="completed",
            status_summary="filter completed",
            result={"items": [{"name": "Ada"}]},
            error=None,
        )


@pytest.mark.asyncio
async def test_mcp_lists_only_executable_capabilities_and_preserves_schema():
    gateway = MCPGateway()
    await gateway.configure(SimpleNamespace(store=_Store()))

    tools = await gateway.server.list_tools()

    assert [item.name for item in tools] == ["joy_catalog_item_filter"]
    assert tools[0].inputSchema["required"] == ["keyword"]
    assert "keyword" in tools[0].inputSchema["properties"]


@pytest.mark.asyncio
async def test_mcp_call_becomes_durable_graph_task_and_returns_run_result():
    gateway = MCPGateway()
    container = SimpleNamespace(
        store=_Store(),
        runs=_Runs(),
        runtime=_Runtime(),
        config=SimpleNamespace(gateway=SimpleNamespace(allow_insecure_auth=True)),
    )
    await gateway.configure(container)

    result = await gateway.server.call_tool("joy_catalog_item_filter", {"keyword": "rl"})

    structured = result[1]
    assert structured["run_id"] == "run_mcp_test"
    assert structured["status"] == "completed"
    assert structured["result"]["items"][0]["name"] == "Ada"


class _PermStore(_Store):
    """Store exposing one tool capability that declares two permissions."""

    def list_capability_definitions(self):
        return [
            {
                "ref": {"capability_id": "catalog.search", "version": "1", "kind": "tool", "plugin_id": "sample-catalog", "plugin_version": "1", "plugin_build_digest": "sha256:test"},
                "name": "Search",
                "description": "Search the catalog",
                "input_schema": {"type": "object"},
                "permissions": ["catalog.search.read", "catalog.search.write"],
                "timeout_seconds": 5,
            },
        ]


class _PartialPermStore(_PermStore):
    def get_platform_admin(self, _user_id):
        return SimpleNamespace(enabled=True, role="admin", permissions=["catalog.search.read"])


class _WildcardPermStore(_PermStore):
    def get_platform_admin(self, _user_id):
        return SimpleNamespace(enabled=True, role="admin", permissions=["catalog.search.*"])


def _container(store):
    return SimpleNamespace(
        store=store,
        runs=_Runs(),
        runtime=_Runtime(),
        config=SimpleNamespace(gateway=SimpleNamespace(allow_insecure_auth=True)),
    )


@pytest.mark.asyncio
async def test_mcp_call_denied_when_only_one_of_two_permissions_held():
    """AND semantics, same as the dispatcher: holding just one declared
    permission must not be enough to invoke through MCP."""
    gateway = MCPGateway()
    await gateway.configure(_container(_PartialPermStore()))

    with pytest.raises(HTTPException) as captured:
        await gateway._invoke(SimpleNamespace(), "catalog.search", {})

    assert captured.value.status_code == 403


@pytest.mark.asyncio
async def test_mcp_call_allowed_with_namespace_wildcard_grant():
    """A `namespace.*` grant covers every declared permission (unified
    wildcard semantics shared with Principal.can and the dispatcher)."""
    gateway = MCPGateway()
    await gateway.configure(_container(_WildcardPermStore()))

    result = await gateway._invoke(SimpleNamespace(), "catalog.search", {})

    assert result["run_id"] == "run_mcp_test"
    assert result["status"] == "completed"


def test_mcp_transport_security_uses_configured_browser_origins():
    gateway = MCPGateway(
        cors_origins=[
            "https://app.example.test",
            "http://localhost:5173",
            "not-an-origin",
            "https://ignored.example.test/path",
            "https://ignored.example.test/?query=1",
        ]
    )

    security = gateway.server.settings.transport_security

    assert security.allowed_origins == ["http://localhost:5173", "https://app.example.test"]
    assert set(security.allowed_hosts) == {
        "127.0.0.1:*",
        "localhost:*",
        "localhost:5173",
        "app.example.test",
    }
