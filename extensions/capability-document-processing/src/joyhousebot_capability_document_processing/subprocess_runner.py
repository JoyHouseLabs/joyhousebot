"""Bounded host subprocess backend for trusted parser code and untrusted documents."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
_WORKER_MODULE = "joyhousebot_capability_document_processing.worker"


def _safe_environment() -> dict[str, str]:
    allowed = {
        name: os.environ[name]
        for name in ("PATH", "LANG", "LC_ALL")
        if name in os.environ
    }
    return {
        **allowed,
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }


def _apply_resource_limits(*, memory_mb: int, cpu_seconds: int) -> None:
    import resource

    limits = [
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_FSIZE, 4 * 1024 * 1024),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_CPU, cpu_seconds),
    ]
    # macOS exposes RLIMIT_AS but rejects setting it in a child pre-exec hook.
    # Linux applies the address-space limit; macOS still receives CPU, file,
    # descriptor, timeout, environment and process-group boundaries.
    if sys.platform != "darwin":
        limits.append((resource.RLIMIT_AS, memory_mb * 1024 * 1024))
    for kind, requested in limits:
        _soft, hard = resource.getrlimit(kind)
        resolved = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
        resource.setrlimit(kind, (resolved, resolved))


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


def _write_inputs(directory: Path, *, body: bytes, request: Mapping[str, Any]) -> None:
    if len(body) > MAX_INPUT_BYTES:
        raise ValueError("document exceeds the subprocess input limit")
    input_path = directory / "source.bin"
    request_path = directory / "request.json"
    input_path.write_bytes(body)
    request_path.write_text(
        json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    input_path.chmod(0o600)
    request_path.chmod(0o600)


def _read_output(directory: Path) -> bytes:
    output = directory / "result.json"
    if output.is_symlink() or not output.is_file():
        raise ValueError("parser subprocess did not produce a regular result file")
    if output.stat().st_size > MAX_OUTPUT_BYTES:
        raise ValueError("parser subprocess result exceeds the output limit")
    return output.read_bytes()


async def run_document_subprocess(
    *,
    body: bytes,
    request: Mapping[str, Any],
    timeout_seconds: int = 120,
    memory_mb: int = 512,
) -> dict[str, Any]:
    """Execute the fixed parser module without a shell and clean staged bytes."""
    if os.name != "posix":
        return {
            "success": False,
            "code": "SUBPROCESS_ISOLATION_UNAVAILABLE",
            "message": "bounded document subprocesses require a POSIX host",
        }
    timeout = max(1, min(int(timeout_seconds), 600))
    memory = max(128, min(int(memory_mb), 2048))
    with tempfile.TemporaryDirectory(prefix="joyhousebot-document-") as temporary:
        directory = Path(temporary)
        try:
            await asyncio.to_thread(_write_inputs, directory, body=body, request=request)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-m",
                _WORKER_MODULE,
                "--input",
                "source.bin",
                "--request",
                "request.json",
                "--output",
                "result.json",
                cwd=directory,
                env=_safe_environment(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
                preexec_fn=lambda: _apply_resource_limits(
                    memory_mb=memory,
                    cpu_seconds=timeout + 1,
                ),
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=float(timeout))
            except asyncio.TimeoutError:
                await _terminate_process_group(process)
                return {
                    "success": False,
                    "code": "PARSER_TIMEOUT",
                    "message": f"document parser exceeded {timeout} seconds",
                    "retryable": False,
                }
            except asyncio.CancelledError:
                await _terminate_process_group(process)
                raise
            if process.returncode != 0:
                return {
                    "success": False,
                    "code": "SUBPROCESS_EXECUTION_FAILED",
                    "message": "document parser subprocess failed",
                    "retryable": False,
                }
            result = await asyncio.to_thread(_read_output, directory)
            return {
                "success": True,
                "output": "(no output)",
                "exit_code": 0,
                "files": {"result.json": result},
            }
        except (OSError, RuntimeError, ValueError) as exc:
            return {
                "success": False,
                "code": "SUBPROCESS_EXECUTION_FAILED",
                "message": str(exc),
                "retryable": False,
            }


__all__ = ["run_document_subprocess"]
