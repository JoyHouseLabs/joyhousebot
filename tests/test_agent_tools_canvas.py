"""Tests for the canvas agent tool."""

import pytest

from joyhousebot.agent.tools.canvas import CanvasTool
from joyhousebot.node import NodeInvokeResult


@pytest.mark.asyncio
async def test_canvas_tool_without_runner_returns_error():
    tool = CanvasTool(node_invoke_runner=None)
    out = await tool.execute(action="hide")
    assert "Error" in out
    assert "not available" in out or "no " in out.lower()


@pytest.mark.asyncio
async def test_canvas_tool_hide_calls_runner():
    seen = []

    async def runner(*, node_id_or_name, command, params, timeout_ms):
        seen.append({"command": command, "params": params})
        return NodeInvokeResult(ok=True)

    tool = CanvasTool(node_invoke_runner=runner)
    out = await tool.execute(action="hide")
    assert "ok" in out
    assert seen == [{"command": "canvas.hide", "params": None}]


@pytest.mark.asyncio
async def test_canvas_tool_navigate_requires_url():
    tool = CanvasTool(node_invoke_runner=lambda **kw: NodeInvokeResult(ok=True))
    out = await tool.execute(action="navigate")
    assert "Error" in out
    assert "url" in out


@pytest.mark.asyncio
async def test_canvas_tool_navigate_calls_runner():
    seen = []

    async def runner(*, node_id_or_name, command, params, timeout_ms):
        seen.append({"command": command, "params": params})
        return NodeInvokeResult(ok=True)

    tool = CanvasTool(node_invoke_runner=runner)
    out = await tool.execute(action="navigate", url="https://example.com/")
    assert "ok" in out
    assert seen == [{"command": "canvas.navigate", "params": {"url": "https://example.com/"}}]


@pytest.mark.asyncio
async def test_canvas_tool_a2ui_push_requires_jsonl_or_path():
    tool = CanvasTool(node_invoke_runner=lambda **kw: NodeInvokeResult(ok=True))
    out = await tool.execute(action="a2ui_push")
    assert "Error" in out
    assert "jsonl" in out


@pytest.mark.asyncio
async def test_canvas_tool_a2ui_reset_calls_runner():
    seen = []

    async def runner(*, node_id_or_name, command, params, timeout_ms):
        seen.append({"command": command})
        return NodeInvokeResult(ok=True)

    tool = CanvasTool(node_invoke_runner=runner)
    out = await tool.execute(action="a2ui_reset")
    assert "ok" in out
    assert seen == [{"command": "canvas.a2ui.reset"}]


@pytest.mark.asyncio
async def test_canvas_tool_runner_error_returned():
    async def runner(*, node_id_or_name, command, params, timeout_ms):
        return NodeInvokeResult(ok=False, error={"code": "NOT_CONNECTED", "message": "no nodes"})

    tool = CanvasTool(node_invoke_runner=runner)
    out = await tool.execute(action="hide")
    assert "Error" in out
    assert "no nodes" in out
