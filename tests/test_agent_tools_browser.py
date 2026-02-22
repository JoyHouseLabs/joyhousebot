"""Tests for the browser agent tool."""

import pytest

from joyhousebot.agent.tools.browser import BrowserTool


@pytest.mark.asyncio
async def test_browser_tool_without_runner_returns_error():
    tool = BrowserTool(browser_request_runner=None)
    out = await tool.execute(action="status")
    assert "Error" in out
    assert "not available" in out or "no runner" in out.lower()


@pytest.mark.asyncio
async def test_browser_tool_status_calls_runner():
    seen = []

    async def runner(*, method: str, path: str, query, body, timeout_ms: int):
        seen.append({"method": method, "path": path})
        return {"running": True, "profile": "default"}

    tool = BrowserTool(browser_request_runner=runner)
    out = await tool.execute(action="status")
    assert "running" in out
    assert seen == [{"method": "GET", "path": "/"}]


@pytest.mark.asyncio
async def test_browser_tool_open_requires_target_url():
    tool = BrowserTool(browser_request_runner=lambda **kw: None)
    out = await tool.execute(action="open")
    assert "Error" in out
    assert "targetUrl" in out


@pytest.mark.asyncio
async def test_browser_tool_act_requires_request_kind():
    tool = BrowserTool(browser_request_runner=lambda **kw: None)
    out = await tool.execute(action="act")
    assert "Error" in out
    assert "request.kind" in out or "kind" in out
