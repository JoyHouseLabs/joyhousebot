"""Regression tests for Library App domain API (App-first: no default Agent in loop)."""

import json

import pytest

from joyhousebot.agent.tools.open_app import OpenAppTool


def test_open_app_tool_returns_navigate_to():
    """OpenAppTool is the only Agent entry for opening domain apps; must return navigate_to."""
    tool = OpenAppTool()
    assert tool.name == "open_app"
    assert "open" in tool.description.lower() and "app" in tool.description.lower()
    assert tool.parameters.get("required") == ["app_id"]


@pytest.mark.asyncio
async def test_open_app_tool_execute_library():
    """open_app(library) must return ok and navigate_to for frontend navigation."""
    tool = OpenAppTool()
    out_str = await tool.execute(app_id="library")
    out = json.loads(out_str)
    assert out.get("ok") is True
    assert out.get("app_id") == "library"
    assert out.get("navigate_to") == "/app/library"


@pytest.mark.asyncio
async def test_open_app_tool_execute_with_route():
    """open_app with route appends to navigate_to (query or hash)."""
    tool = OpenAppTool()
    out_str = await tool.execute(app_id="library", route="/add")
    out = json.loads(out_str)
    assert out.get("ok") is True
    assert "/app/library" in out.get("navigate_to", "")
    assert "route=" in out.get("navigate_to", "") or "/add" in out.get("navigate_to", "")


@pytest.mark.asyncio
async def test_open_app_tool_execute_missing_app_id():
    """Missing app_id returns ok: false and INVALID_REQUEST."""
    tool = OpenAppTool()
    out_str = await tool.execute(app_id="")
    out = json.loads(out_str)
    assert out.get("ok") is False
    assert out.get("error", {}).get("code") == "INVALID_REQUEST"
