"""Fail-closed execution of one-off commands in Docker containers."""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

# Cache `docker info` briefly so every exec does not pay a daemon round-trip.
_DOCKER_INFO_TTL_SECONDS = 60.0
_docker_info_cache: tuple[float, bool] | None = None

# Per-stream (stdout/stderr) output cap; exceeding it truncates and stops the container.
_MAX_OUTPUT_BYTES = 1024 * 1024  # 1 MB


async def _probe_docker() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "info",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10.0)
        return proc.returncode == 0
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return False


async def is_docker_available() -> bool:
    """Return True if docker CLI is available and daemon is reachable.

    The result is cached for ``_DOCKER_INFO_TTL_SECONDS`` to avoid running
    ``docker info`` before every single exec.
    """
    global _docker_info_cache
    now = time.monotonic()
    if _docker_info_cache is not None and now - _docker_info_cache[0] < _DOCKER_INFO_TTL_SECONDS:
        return _docker_info_cache[1]
    result = await _probe_docker()
    _docker_info_cache = (now, result)
    return result


def _escape_single(s: str) -> str:
    """Escape for single-quoted shell (replace ' with '\'')."""
    return s.replace("'", "'\"'\"'")


async def _kill_container(name: str) -> None:
    """Best-effort cleanup of a named container: kill, then force-remove as fallback."""
    for args in (("docker", "kill", name), ("docker", "rm", "-f", name)):
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10.0)
            if proc.returncode == 0:
                return
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            continue


async def run_in_container(
    *,
    command: str,
    cwd: str,
    timeout_seconds: int,
    image: str,
    workspace_host_path: str,
    workspace_container_path: str = "/workspace",
    user: str = "",
    network: str = "none",
    shell_mode: bool = False,
    memory: str = "512m",
    cpus: str = "1",
    pids_limit: int = 256,
    max_output_bytes: int = _MAX_OUTPUT_BYTES,
) -> tuple[str, int, str | None]:
    """
    Run command inside a one-off container (docker run --rm).
    Always uses sh -c so piping/redirects work. Returns (combined_stdout_stderr, exit_code, error_message_if_failed).

    The container runs with resource limits (memory/cpus/pids), dropped capabilities,
    no-new-privileges and a small noexec /tmp. It is named so that on timeout or output
    overflow the container itself is killed (killing the docker CLI alone would leave it
    running). Output per stream is capped at ``max_output_bytes``; beyond that the output
    is truncated, marked with "[output truncated]" and the container is stopped.
    """
    host_workspace = Path(workspace_host_path or cwd).expanduser().resolve()
    if not host_workspace.exists():
        return "", -1, f"Workspace path does not exist: {host_workspace}"
    host_ws = str(host_workspace)
    if not host_ws.strip():
        return "", -1, "Workspace path is empty"
    cmd_escaped = _escape_single(command)
    name = f"joyhousebot-exec-{uuid.uuid4().hex[:12]}"
    args = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--memory",
        memory,
        "--cpus",
        cpus,
        "--pids-limit",
        str(pids_limit),
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "-v",
        f"{host_ws}:{workspace_container_path}",
        "-w",
        workspace_container_path,
        "--network",
        network,
    ]
    if user and user.strip():
        args.extend(["--user", user.strip()])
    args.extend([image, "sh", "-c", cmd_escaped])
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return "", -1, "Docker CLI not found"
    except Exception as e:
        return "", -1, str(e)

    truncated = False

    async def _read(stream: asyncio.StreamReader) -> bytes:
        nonlocal truncated
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > max_output_bytes:
                truncated = True
                keep = max_output_bytes - (size - len(chunk))
                if keep > 0:
                    chunks.append(chunk[:keep])
                await _kill_container(name)
                break
            chunks.append(chunk)
        return b"".join(chunks)

    out_task = asyncio.ensure_future(_read(proc.stdout))
    err_task = asyncio.ensure_future(_read(proc.stderr))
    try:
        await asyncio.wait_for(
            asyncio.gather(out_task, err_task), timeout=float(timeout_seconds)
        )
        await proc.wait()
    except asyncio.TimeoutError:
        # Killing the docker CLI alone leaves the container running; kill it by name.
        await _kill_container(name)
        proc.kill()
        await proc.wait()
        await asyncio.gather(out_task, err_task, return_exceptions=True)
        return "", -1, f"Command timed out after {timeout_seconds} seconds"
    except Exception as e:
        await _kill_container(name)
        proc.kill()
        await asyncio.gather(out_task, err_task, return_exceptions=True)
        return "", -1, str(e)

    out = (out_task.result() + err_task.result()).decode("utf-8", errors="replace")
    if truncated:
        out += "\n[output truncated: per-stream limit reached, container stopped]"
    return out, proc.returncode or 0, None if proc.returncode == 0 else out
