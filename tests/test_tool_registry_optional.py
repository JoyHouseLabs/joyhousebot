from typing import Any

import pytest

from joyhousebot.agent.tools.base import Tool
from joyhousebot.agent.tools.registry import ToolRegistry


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


def test_optional_tools_enabled_by_default_without_allowlist():
    registry = ToolRegistry()
    registry.register(_DummyTool("core"))
    registry.register(_DummyTool("optional.web"), optional=True)
    assert registry.has("core")
    assert registry.has("optional.web")
    assert "optional.web" in registry.tool_names


def test_optional_tools_can_be_gated_by_allowlist():
    registry = ToolRegistry(optional_allowlist=["optional.allowed"])
    registry.register(_DummyTool("core"))
    registry.register(_DummyTool("optional.blocked"), optional=True)
    registry.register(_DummyTool("optional.allowed"), optional=True)
    assert registry.has("core")
    assert not registry.has("optional.blocked")
    assert registry.has("optional.allowed")
    names = [row["function"]["name"] for row in registry.get_definitions()]
    assert "optional.blocked" not in names
    assert "optional.allowed" in names


@pytest.mark.asyncio
async def test_execute_returns_disabled_error_for_blocked_optional_tool():
    registry = ToolRegistry(optional_allowlist=["optional.allowed"])
    registry.register(_DummyTool("optional.blocked"), optional=True)
    result = await registry.execute("optional.blocked", {})
    assert "disabled" in result

