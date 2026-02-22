"""Tests for sandbox docker_backend (availability and run_in_container)."""

import pytest

from joyhousebot.sandbox.docker_backend import is_docker_available, run_in_container


@pytest.mark.asyncio
async def test_is_docker_available_returns_bool():
    """is_docker_available returns True or False."""
    out = await is_docker_available()
    assert isinstance(out, bool)


@pytest.mark.asyncio
async def test_run_in_container_missing_workspace():
    """run_in_container returns error when workspace path does not exist."""
    out, code, err = await run_in_container(
        command="echo x",
        cwd="/tmp",
        timeout_seconds=5,
        image="alpine:3.18",
        workspace_host_path="/nonexistent_path_xyz_123",
    )
    assert err is not None
    assert "exist" in err.lower() or "not found" in err.lower() or "empty" in err.lower()


@pytest.mark.asyncio
async def test_run_in_container_success_when_docker_available():
    """When Docker is available and workspace exists, run_in_container runs command."""
    avail = await is_docker_available()
    if not avail:
        pytest.skip("Docker not available")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out, code, err = await run_in_container(
            command="echo hello",
            cwd=d,
            timeout_seconds=30,
            image="alpine:3.18",
            workspace_host_path=d,
        )
        if err is not None and ("Unable to find image" in err or "failed to resolve" in err or "EOF" in err):
            pytest.skip("Docker image pull failed (network/registry)")
        assert err is None, err
        assert "hello" in (out or "")
        assert code == 0
