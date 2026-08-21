"""Tests for sandbox docker_backend (availability and run_in_container)."""

import asyncio

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
        if err is not None and (
            "Unable to find image" in err or "failed to resolve" in err or "EOF" in err
        ):
            pytest.skip("Docker image pull failed (network/registry)")
        assert err is None, err
        assert "hello" in (out or "")
        assert code == 0


class _FakeStream:
    def __init__(self, data: bytes = b"", hang: bool = False):
        self._data = data
        self._hang = hang

    async def read(self, n: int = -1) -> bytes:
        if self._hang:
            await asyncio.sleep(3600)
            return b""
        if not self._data:
            return b""
        if n is None or n < 0:
            n = len(self._data)
        chunk, self._data = self._data[:n], self._data[n:]
        return chunk


class _FakeProc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0, hang: bool = False):
        self.stdout = _FakeStream(stdout, hang)
        self.stderr = _FakeStream(stderr, hang)
        self.returncode = returncode
        self.killed = False

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class _DockerHarness:
    """Fake asyncio.create_subprocess_exec: records calls, fakes docker run/kill/rm."""

    def __init__(self, run_proc: _FakeProc):
        self.calls: list[list[str]] = []
        self.run_proc = run_proc

    async def __call__(self, *args, **_kwargs):
        self.calls.append(list(args))
        if args[:2] == ("docker", "run"):
            return self.run_proc
        return _FakeProc()  # docker kill / docker rm -f


@pytest.mark.asyncio
async def test_is_docker_available_uses_ttl_cache(monkeypatch):
    """docker info is probed once per TTL window, not before every exec."""
    import joyhousebot.sandbox.docker_backend as backend

    calls = 0

    async def probe() -> bool:
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(backend, "_docker_info_cache", None)
    monkeypatch.setattr(backend, "_probe_docker", probe)
    assert await backend.is_docker_available() is True
    assert await backend.is_docker_available() is True
    assert calls == 1


@pytest.mark.asyncio
async def test_run_in_container_applies_limits_and_names_container(monkeypatch, tmp_path):
    """docker run gets resource limits, hardening flags and a joyhousebot-exec-* name."""
    harness = _DockerHarness(_FakeProc(stdout=b"hi\n"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", harness)

    out, code, err = await run_in_container(
        command="echo hi",
        cwd=str(tmp_path),
        timeout_seconds=5,
        image="alpine:3.18",
        workspace_host_path=str(tmp_path),
    )
    assert err is None and code == 0 and "hi" in out
    run_args = harness.calls[0]
    assert run_args[run_args.index("--name") + 1].startswith("joyhousebot-exec-")
    assert run_args[run_args.index("--memory") + 1] == "512m"
    assert run_args[run_args.index("--cpus") + 1] == "1"
    assert run_args[run_args.index("--pids-limit") + 1] == "256"
    assert run_args[run_args.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in run_args
    assert any(a.startswith("/tmp:rw,noexec,nosuid") for a in run_args)


@pytest.mark.asyncio
async def test_run_in_container_timeout_kills_named_container(monkeypatch, tmp_path):
    """On timeout the container itself is killed by name (not just the docker CLI)."""
    proc = _FakeProc(hang=True)
    harness = _DockerHarness(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", harness)

    _out, _code, err = await run_in_container(
        command="sleep 60",
        cwd=str(tmp_path),
        timeout_seconds=0.2,
        image="alpine:3.18",
        workspace_host_path=str(tmp_path),
    )
    assert err is not None and "timed out" in err
    run_args = harness.calls[0]
    name = run_args[run_args.index("--name") + 1]
    assert ["docker", "kill", name] in harness.calls
    assert proc.killed


@pytest.mark.asyncio
async def test_run_in_container_truncates_oversized_output(monkeypatch, tmp_path):
    """Output beyond 1MB per stream is truncated and the container is stopped."""
    big = b"x" * (1024 * 1024 + 100)
    harness = _DockerHarness(_FakeProc(stdout=big))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", harness)

    out, _code, _err = await run_in_container(
        command="cat bigfile",
        cwd=str(tmp_path),
        timeout_seconds=5,
        image="alpine:3.18",
        workspace_host_path=str(tmp_path),
    )
    assert "output truncated" in out
    assert out.count("x") <= 1024 * 1024
    run_args = harness.calls[0]
    name = run_args[run_args.index("--name") + 1]
    assert ["docker", "kill", name] in harness.calls
