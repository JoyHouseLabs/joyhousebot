"""Minimal Docker backend: availability check, run in container, list/remove."""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path
from typing import Any

SANDBOX_LABEL = "joyhousebot.sandbox=1"


async def is_docker_available() -> bool:
    """Return True if docker CLI is available and daemon is reachable."""
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


def _escape_single(s: str) -> str:
    """Escape for single-quoted shell (replace ' with '\'')."""
    return s.replace("'", "'\"'\"'")


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
) -> tuple[str, int, str | None]:
    """
    Run command inside a one-off container (docker run --rm).
    Always uses sh -c so piping/redirects work. Returns (combined_stdout_stderr, exit_code, error_message_if_failed).
    """
    host_workspace = Path(workspace_host_path or cwd).expanduser().resolve()
    if not host_workspace.exists():
        return "", -1, f"Workspace path does not exist: {host_workspace}"
    host_ws = str(host_workspace)
    if not host_ws.strip():
        return "", -1, "Workspace path is empty"
    cmd_escaped = _escape_single(command)
    args = [
        "docker",
        "run",
        "--rm",
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
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=float(timeout_seconds))
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return "", -1, f"Command timed out after {timeout_seconds} seconds"
        out = stdout.decode("utf-8", errors="replace")
        return out, proc.returncode or 0, None if proc.returncode == 0 else out
    except FileNotFoundError:
        return "", -1, "Docker CLI not found"
    except asyncio.TimeoutError:
        return "", -1, "Docker run timed out"
    except Exception as e:
        return "", -1, str(e)


async def list_containers(browser_only: bool = False) -> list[dict[str, Any]]:
    """List containers with label joyhousebot.sandbox=1. Returns list of {id, names, image, labels, browser?}."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label={SANDBOX_LABEL}",
            "--format",
            "{{json .}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        if proc.returncode != 0:
            return []
        out: list[dict[str, Any]] = []
        for line in stdout.decode("utf-8", errors="replace").strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            cid = obj.get("ID") or obj.get("Id") or ""
            names = obj.get("Names") or ""
            image = obj.get("Image") or ""
            labels = obj.get("Labels") or ""
            browser = "browser" in (labels or "").lower() or "browser" in (names or "").lower()
            if browser_only and not browser:
                continue
            out.append({
                "id": cid[:12] if len(cid) > 12 else cid,
                "idFull": cid,
                "names": names,
                "image": image,
                "browser": browser,
            })
        return out
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return []


async def remove_container(container_id: str) -> tuple[bool, str]:
    """Remove container by id (docker rm -f). Returns (success, error_message)."""
    if not container_id or not container_id.strip():
        return False, "empty container id"
    cid = container_id.strip()
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            cid,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            return False, err or f"exit code {proc.returncode}"
        return True, ""
    except FileNotFoundError:
        return False, "Docker CLI not found"
    except asyncio.TimeoutError:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)
