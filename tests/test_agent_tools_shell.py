"""Tests for fail-closed sandbox execution."""

import pytest

from joyhousebot.agent.tools.shell import ExecTool
from joyhousebot.capabilities.tool_adapter import ToolInvocationError


@pytest.mark.asyncio
async def test_exec_tool_guard_blocks_dangerous():
    """Deny patterns block dangerous commands."""
    tool = ExecTool(
        working_dir="/tmp",
        timeout=5,
        restrict_to_workspace=False,
        container_workspace_mount="/tmp",
    )
    with pytest.raises(ToolInvocationError, match="blocked") as captured:
        await tool.execute("rm -rf /")
    assert captured.value.code == "COMMAND_BLOCKED"


@pytest.mark.asyncio
async def test_exec_tool_never_falls_back_to_host_when_sandbox_is_unavailable(
    monkeypatch,
):
    async def unavailable() -> bool:
        return False

    monkeypatch.setattr(
        "joyhousebot.sandbox.docker_backend.is_docker_available",
        unavailable,
    )
    tool = ExecTool(
        working_dir="/tmp",
        timeout=5,
        restrict_to_workspace=True,
    )
    with pytest.raises(ToolInvocationError, match="sandbox is unavailable") as captured:
        await tool.execute("echo must-not-run-on-host")
    assert captured.value.code == "SANDBOX_UNAVAILABLE"


def test_exec_tool_defaults_to_restricted() -> None:
    """restrict_to_workspace defaults to True (cloud-safe)."""
    tool = ExecTool(working_dir="/tmp", timeout=5)
    assert tool.restrict_to_workspace is True


@pytest.mark.asyncio
async def test_exec_tool_unrestricted_requires_explicit_mount() -> None:
    """restrict_to_workspace=False without container_workspace_mount fails closed."""
    tool = ExecTool(working_dir="/tmp", timeout=5, restrict_to_workspace=False)
    with pytest.raises(ToolInvocationError, match="container_workspace_mount"):
        await tool.execute("echo hi")


@pytest.mark.asyncio
async def test_exec_tool_rejects_platform_cwd_as_mount_source(tmp_path, monkeypatch) -> None:
    """The platform process working directory must not be used as the mount source."""
    monkeypatch.chdir(tmp_path)
    tool = ExecTool(
        working_dir="/tmp",
        timeout=5,
        restrict_to_workspace=False,
        container_workspace_mount=str(tmp_path),
    )
    with pytest.raises(ToolInvocationError, match="container_workspace_mount"):
        await tool.execute("echo hi")


def test_exec_tool_container_network_host_falls_back_to_none() -> None:
    """container_network='host' is rejected on the cloud platform."""
    tool = ExecTool(working_dir="/tmp", timeout=5, container_network="host")
    assert tool.container_network == "none"


@pytest.mark.asyncio
async def test_exec_tool_guard_blocks_embedded_newlines():
    """Newlines/CRs count as shell metacharacters in restricted non-shell mode."""
    tool = ExecTool(working_dir="/tmp", timeout=5, restrict_to_workspace=True)
    with pytest.raises(ToolInvocationError, match="shell metacharacters are not allowed"):
        await tool.execute("echo ok\nls -la")
    with pytest.raises(ToolInvocationError, match="shell metacharacters are not allowed"):
        await tool.execute("echo ok\recho nope")


def test_exec_config_secure_defaults() -> None:
    """ExecToolConfig defaults to the hardened sandbox profile."""
    from joyhousebot.config.schema import ExecToolConfig

    cfg = ExecToolConfig()
    assert cfg.container_user == "65534:65534"
    assert cfg.container_network == "none"
    assert cfg.container_memory == "512m"
    assert cfg.container_cpus == "1"
    assert cfg.container_pids_limit == 256


@pytest.mark.asyncio
async def test_scratch_cleanup_removes_stale_dirs(tmp_path) -> None:
    """Creating a run scratch dir opportunistically removes dirs older than 24h."""
    import os
    import time

    from joyhousebot.runtime.context import ToolExecutionContext

    tool = ExecTool(working_dir=str(tmp_path), timeout=5, restrict_to_workspace=True)
    scratch = tmp_path / ".scratch"
    stale = scratch / "stale-run"
    stale.mkdir(parents=True)
    old_time = time.time() - 25 * 3600
    os.utime(stale, (old_time, old_time))

    context = ToolExecutionContext(
        run_id="run-1",
        root_run_id="root-1",
        session_key="s",
        channel="api",
        chat_id="c",
    )
    tool._scoped_working_dir(str(tmp_path), context)

    assert not stale.exists()
    remaining = [p for p in scratch.iterdir() if p.is_dir()]
    assert len(remaining) == 1  # only the fresh run scope remains


def test_exec_tool_guard_allows_container_workspace_paths(tmp_path) -> None:
    """Paths the container actually sees (/workspace mount, /tmp tmpfs) pass."""
    tool = ExecTool(working_dir=str(tmp_path), timeout=5, restrict_to_workspace=True)
    assert tool._guard_command("cat /workspace/result.txt", str(tmp_path)) is None
    assert tool._guard_command("ls /workspace", str(tmp_path)) is None
    assert tool._guard_command("ls /tmp", str(tmp_path)) is None


def test_exec_tool_guard_blocks_host_absolute_paths(tmp_path) -> None:
    """Host paths (including paths under the host working_dir, e.g. another
    run's scratch dir) are meaningless inside the container and are rejected
    with a UX hint instead of being silently passed or wrongly allowed."""
    tool = ExecTool(working_dir=str(tmp_path), timeout=5, restrict_to_workspace=True)

    err = tool._guard_command(f"cat {tmp_path}/.scratch/other-run/secret", str(tmp_path))
    assert err and "not visible inside the execution container" in err

    err = tool._guard_command("cat /etc/passwd", str(tmp_path))
    assert err and "not visible inside the execution container" in err


def test_exec_tool_guard_blocks_windows_paths(tmp_path) -> None:
    tool = ExecTool(working_dir=str(tmp_path), timeout=5, restrict_to_workspace=True)
    err = tool._guard_command("type C:\\Users\\x\\secret.txt", str(tmp_path))
    assert err and "Windows paths" in err


def test_exec_tool_guard_still_blocks_path_traversal(tmp_path) -> None:
    tool = ExecTool(working_dir=str(tmp_path), timeout=5, restrict_to_workspace=True)
    err = tool._guard_command("cat ../secret", str(tmp_path))
    assert err and "path traversal" in err


def test_exec_tool_guard_still_checks_cwd_mount_source(tmp_path) -> None:
    """cwd becomes the container mount source; outside working_dir is denied."""
    tool = ExecTool(working_dir=str(tmp_path), timeout=5, restrict_to_workspace=True)
    err = tool._guard_command("echo hi", "/var/lib/other")
    assert err and "working_dir outside allowed root" in err
