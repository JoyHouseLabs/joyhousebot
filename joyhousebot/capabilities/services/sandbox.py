"""Fail-closed container sandbox port."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from joyhousebot.contracts.capabilities import CapabilityContext
from joyhousebot.sandbox import guard_shell_command, is_docker_available, run_in_container

from .scratch import ScratchPort


class SandboxPort:
    def __init__(self, scratch: ScratchPort) -> None:
        self._scratch = scratch

    async def execute(
        self,
        context: CapabilityContext,
        *,
        command: str,
        working_dir: str = ".",
        configuration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = dict(configuration or {})
        root = self._scratch.resolve(context, ".")
        cwd = self._scratch.resolve(context, working_dir or ".")
        cwd.mkdir(parents=True, exist_ok=True)
        try:
            root.chmod(0o777)
            cwd.chmod(0o777)
        except OSError:
            pass
        shell_mode = bool(settings.get("shell_mode", False))
        error = guard_shell_command(
            command,
            str(cwd),
            working_dir=str(root),
            restrict_to_workspace=True,
            shell_mode=shell_mode,
            deny_patterns=tuple(settings.get("deny_patterns") or ()),
            allow_patterns=tuple(settings.get("allow_patterns") or ()),
        )
        if error:
            return {"success": False, "code": "COMMAND_BLOCKED", "message": error}
        try:
            if not await is_docker_available():
                return {
                    "success": False,
                    "code": "SANDBOX_UNAVAILABLE",
                    "message": "execution sandbox is unavailable",
                    "retryable": True,
                }
        except Exception as exc:
            return {
                "success": False,
                "code": "SANDBOX_CHECK_FAILED",
                "message": str(exc),
                "retryable": True,
            }

        timeout_seconds = max(1, min(3600, int(settings.get("timeout") or 60)))
        network = str(settings.get("container_network") or "none")
        if network != "none":
            return {
                "success": False,
                "code": "SANDBOX_MISCONFIGURED",
                "message": "sandbox permits only container_network=none",
            }
        try:
            output, exit_code, execution_error = await run_in_container(
                command=command,
                cwd=str(cwd),
                timeout_seconds=timeout_seconds,
                image=str(settings.get("container_image") or "alpine:3.18"),
                workspace_host_path=str(cwd),
                workspace_container_path="/workspace",
                user=str(settings.get("container_user") or "65534:65534"),
                network=network,
                shell_mode=shell_mode,
                memory=str(settings.get("container_memory") or "512m"),
                cpus=str(settings.get("container_cpus") or "1"),
                pids_limit=max(16, min(4096, int(settings.get("container_pids_limit") or 256))),
            )
        except Exception as exc:
            return {"success": False, "code": "SANDBOX_EXECUTION_FAILED", "message": str(exc)}
        if execution_error is not None:
            code = (
                "COMMAND_TIMEOUT"
                if "timed out" in execution_error.lower()
                else "SANDBOX_EXECUTION_FAILED"
            )
            return {
                "success": False,
                "code": code,
                "message": execution_error,
                "retryable": code == "COMMAND_TIMEOUT",
                "exit_code": exit_code,
            }
        return {
            "success": True,
            "output": (output or "(no output)").rstrip(),
            "exit_code": exit_code,
        }

    async def execute_job(
        self,
        context: CapabilityContext,
        *,
        command: str,
        input_files: dict[str, bytes],
        output_files: tuple[str, ...],
        configuration: dict[str, Any] | None = None,
        max_input_bytes: int = 25 * 1024 * 1024,
        max_output_bytes: int = 2 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Run a bounded file-in/file-out job and always remove staged files."""
        names = (*input_files.keys(), *output_files)
        if not names or any(not _safe_job_file_name(name) for name in names):
            return {
                "success": False,
                "code": "SANDBOX_JOB_INVALID",
                "message": "sandbox job file names must be safe relative paths",
            }
        if len(set(names)) != len(names):
            return {
                "success": False,
                "code": "SANDBOX_JOB_INVALID",
                "message": "sandbox job input and output names must be unique",
            }
        total_input = sum(len(value) for value in input_files.values())
        input_limit = min(max(1, int(max_input_bytes)), 26 * 1024 * 1024)
        if total_input > input_limit:
            return {
                "success": False,
                "code": "SANDBOX_JOB_INPUT_LIMIT",
                "message": "sandbox job input exceeds the configured limit",
            }

        workspace = f".sandbox-jobs/{uuid4().hex}"
        directory = self._scratch.resolve(context, workspace)
        directory.mkdir(parents=True, exist_ok=False)
        try:
            for name, value in input_files.items():
                target = directory / name
                target.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(target.write_bytes, value)
                await asyncio.to_thread(os.chmod, target, 0o644)
            result = await self.execute(
                context,
                command=command,
                working_dir=workspace,
                configuration=configuration,
            )
            if not result.get("success"):
                return result
            collected: dict[str, bytes] = {}
            output_limit = min(max(1, int(max_output_bytes)), 4 * 1024 * 1024)
            for name in output_files:
                target = directory / name
                try:
                    target.resolve().relative_to(directory.resolve())
                except ValueError:
                    return {
                        "success": False,
                        "code": "SANDBOX_JOB_OUTPUT_INVALID",
                        "message": f"sandbox job output {name} escapes the job workspace",
                    }
                if target.is_symlink() or not target.is_file():
                    return {
                        "success": False,
                        "code": "SANDBOX_JOB_OUTPUT_INVALID",
                        "message": f"sandbox job did not produce a regular {name}",
                    }
                size = target.stat().st_size
                if size > output_limit:
                    return {
                        "success": False,
                        "code": "SANDBOX_JOB_OUTPUT_LIMIT",
                        "message": f"sandbox job output {name} exceeds the configured limit",
                    }
                collected[name] = await asyncio.to_thread(target.read_bytes)
            return {**result, "files": collected}
        finally:
            await asyncio.to_thread(shutil.rmtree, directory, True)


def _safe_job_file_name(value: str) -> bool:
    name = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(name)
    return (
        bool(name)
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


__all__ = ["SandboxPort"]
