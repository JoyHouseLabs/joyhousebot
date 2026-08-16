from typing import Any

import pytest

from porthouse.capabilities import CapabilityRegistry
from porthouse.capabilities.dispatcher import capability_result_prompt
from porthouse.contracts.tools import Tool
from porthouse.runtime.context import ToolExecutionContext
from tests.support.capabilities import register_tool_fixture


class _DummyTool(Tool):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "dummy"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def test_optional_tools_fail_closed_without_allowlist():
    registry = CapabilityRegistry()
    core = _DummyTool("core")
    optional = _DummyTool("optional.web")
    register_tool_fixture(registry, core)
    register_tool_fixture(registry, optional, optional=True)
    assert registry.has("core")
    assert not registry.has("optional.web")
    assert "optional.web" not in registry.tool_names


def test_optional_tools_can_be_gated_by_allowlist():
    registry = CapabilityRegistry(optional_allowlist=["optional.allowed"])
    core = _DummyTool("core")
    blocked = _DummyTool("optional.blocked")
    allowed = _DummyTool("optional.allowed")
    register_tool_fixture(registry, core)
    register_tool_fixture(registry, blocked, optional=True)
    register_tool_fixture(registry, allowed, optional=True)
    assert registry.has("core")
    assert not registry.has("optional.blocked")
    assert registry.has("optional.allowed")
    names = [row["function"]["name"] for row in registry.get_tool_definitions()]
    assert "optional.blocked" not in names
    assert "optional.allowed" in names


@pytest.mark.asyncio
async def test_execute_returns_disabled_error_for_blocked_optional_tool():
    registry = CapabilityRegistry(optional_allowlist=["optional.allowed"])
    blocked = _DummyTool("optional.blocked")
    register_tool_fixture(registry, blocked, optional=True)
    result = await registry.invoke_tool(
        "optional.blocked",
        {},
        context=ToolExecutionContext("run", "session", "api", "chat"),
    )
    assert "disabled" in capability_result_prompt(result)
