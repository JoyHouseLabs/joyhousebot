"""Tests for code_runner tool and code backends."""

import asyncio
import pytest

from joyhousebot.agent.tools.code_backends.base import CodeBackend, RunResult
from joyhousebot.agent.tools.code_backends.claude_code_backend import ClaudeCodeBackend
from joyhousebot.agent.tools.code_runner import CodeRunnerTool


def test_run_result_to_display_string():
    r = RunResult(
        backend_id="claude_code",
        mode="host",
        success=True,
        exit_code=0,
        stdout="Hello",
        stderr="",
    )
    out = r.to_display_string()
    assert "[backend=claude_code mode=host exit_code=0]" in out
    assert "Hello" in out
    assert "Fallback" not in out


def test_run_result_to_display_string_with_fallback_and_error():
    r = RunResult(
        backend_id="claude_code",
        mode="host",
        success=False,
        exit_code=-1,
        stdout="",
        stderr="",
        error_message="Docker unavailable",
        fallback_used=True,
    )
    out = r.to_display_string()
    assert "Fallback: ran in host" in out
    assert "Error: Docker unavailable" in out
    assert "exit_code=-1" in out


def test_run_result_truncates_long_stdout():
    r = RunResult(
        backend_id="x",
        mode="host",
        success=True,
        exit_code=0,
        stdout="a" * 15000,
        stderr="",
    )
    out = r.to_display_string(max_stdout=100)
    assert "... (truncated" in out
    assert len(out) < 500


def test_code_runner_tool_validate_params():
    tool = CodeRunnerTool()
    errors = tool.validate_params({})
    assert any("prompt" in e for e in errors)
    errors = tool.validate_params({"prompt": "do something"})
    assert not errors


def test_code_runner_tool_unknown_backend():
    tool = CodeRunnerTool(default_backend="claude_code")
    # Backend id from params overrides default; we pass unknown backend via execute
    result = asyncio.run(tool.execute(prompt="hi", backend="unknown_backend"))
    assert "Unknown backend" in result
    assert "claude_code" in result


@pytest.mark.asyncio
async def test_claude_code_backend_id():
    b = ClaudeCodeBackend(command="claude")
    assert b.backend_id == "claude_code"


@pytest.mark.asyncio
async def test_claude_code_backend_run_host_cli_not_found():
    """When host run fails (e.g. CLI not found), we get a RunResult indicating failure."""
    b = ClaudeCodeBackend(command="nonexistent_claude_cmd_xyz_12345")
    result = await b.run(prompt="hello", mode="host", timeout=2)
    assert result.backend_id == "claude_code"
    assert result.mode == "host"
    assert not result.success or result.exit_code != 0
    # Either error_message is set (FileNotFoundError) or stderr contains shell "not found"
    has_hint = (
        (result.error_message and ("not found" in result.error_message.lower() or "nonexistent" in result.error_message.lower()))
        or (result.stderr and "not found" in result.stderr.lower())
    )
    assert has_hint or result.exit_code != 0


@pytest.mark.asyncio
async def test_claude_code_backend_run_container_no_image():
    """When mode=container and container_image is empty, return error without calling Docker."""
    b = ClaudeCodeBackend()
    result = await b.run(prompt="hi", mode="container", container_image="")
    assert result.mode == "container"
    assert not result.success
    assert "container" in (result.error_message or "").lower() or "image" in (result.error_message or "").lower()


@pytest.mark.asyncio
async def test_code_runner_execute_returns_display_string():
    """code_runner.execute returns the backend RunResult formatted as string."""
    tool = CodeRunnerTool(default_backend="claude_code", default_mode="host", timeout=2)
    # Use nonexistent command so run fails quickly; we only check formatted output
    tool._claude_code_command = "nonexistent_code_runner_cmd_xyz"
    out = await tool.execute(prompt="say 1+1", timeout=2)
    assert "[backend=claude_code" in out or "Error:" in out
    assert "mode=" in out or "exit_code" in out or "Error" in out


@pytest.mark.asyncio
async def test_code_runner_require_approval_deny():
    """When require_approval=True and approval_request_fn returns 'deny', execute returns error."""
    async def deny(_cmd: str, _timeout_ms: int = 120_000, _req_id: str | None = None) -> str:
        return "deny"

    tool = CodeRunnerTool(
        default_backend="claude_code",
        default_mode="host",
        timeout=2,
        require_approval=True,
        approval_request_fn=deny,
    )
    tool._claude_code_command = "nonexistent_cmd"
    out = await tool.execute(prompt="hello")
    assert "Approval denied" in out


@pytest.mark.asyncio
async def test_code_runner_require_approval_expired():
    """When require_approval=True and approval_request_fn returns None, execute returns expiry message with id."""
    async def expire(_cmd: str, _timeout_ms: int = 120_000, _req_id: str | None = None) -> str | None:
        return None

    tool = CodeRunnerTool(
        default_backend="claude_code",
        default_mode="host",
        timeout=2,
        require_approval=True,
        approval_request_fn=expire,
    )
    tool._claude_code_command = "nonexistent_cmd"
    out = await tool.execute(prompt="hello")
    assert "Approval expired" in out or "not granted" in out
    assert "approvals resolve" in out
    assert "allow-once" in out


@pytest.mark.asyncio
async def test_code_runner_require_approval_allow_runs():
    """When require_approval=True and approval_request_fn returns allow-once, backend runs."""
    async def allow_once(_cmd: str, _timeout_ms: int = 120_000, _req_id: str | None = None) -> str:
        return "allow-once"

    tool = CodeRunnerTool(
        default_backend="claude_code",
        default_mode="host",
        timeout=2,
        require_approval=True,
        approval_request_fn=allow_once,
    )
    tool._claude_code_command = "nonexistent_code_runner_cmd_xyz"
    out = await tool.execute(prompt="say 1+1", timeout=2)
    # Backend was invoked (may fail due to missing CLI, but we get backend output or error)
    assert "[backend=claude_code" in out or "Error:" in out


@pytest.mark.asyncio
async def test_code_runner_require_approval_no_fn_skips_gate():
    """When require_approval=True but approval_request_fn is None, no approval gate (runs directly)."""
    tool = CodeRunnerTool(
        default_backend="claude_code",
        default_mode="host",
        timeout=2,
        require_approval=True,
        approval_request_fn=None,
    )
    tool._claude_code_command = "nonexistent_code_runner_cmd_xyz"
    out = await tool.execute(prompt="hi", timeout=2)
    assert "Approval denied" not in out
    assert "Approval expired" not in out
    assert "[backend=claude_code" in out or "Error:" in out
