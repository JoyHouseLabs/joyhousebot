"""Tests for ExecTool (direct and Docker backend with fallback)."""

import pytest

from joyhousebot.agent.tools.shell import ExecTool


@pytest.mark.asyncio
async def test_exec_tool_direct():
    """Without container_enabled, command runs on host."""
    tool = ExecTool(
        working_dir="/tmp",
        timeout=5,
        restrict_to_workspace=False,
        shell_mode=True,
        container_enabled=False,
    )
    out = await tool.execute("echo ok")
    assert "ok" in out
    assert "Sandbox fallback" not in out


@pytest.mark.asyncio
async def test_exec_tool_guard_blocks_dangerous():
    """Deny patterns block dangerous commands."""
    tool = ExecTool(
        working_dir="/tmp",
        timeout=5,
        restrict_to_workspace=False,
        container_enabled=False,
    )
    out = await tool.execute("rm -rf /")
    assert "blocked" in out.lower() or "Error" in out


@pytest.mark.asyncio
async def test_exec_tool_container_disabled_same_as_direct():
    """With container_enabled=False, behavior is same as direct."""
    tool = ExecTool(
        working_dir="/tmp",
        timeout=5,
        restrict_to_workspace=False,
        shell_mode=True,
        container_enabled=False,
    )
    out = await tool.execute("echo direct")
    assert "direct" in out
