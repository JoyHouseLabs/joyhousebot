"""Fail-closed container sandbox port."""

from __future__ import annotations

from typing import Any

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


__all__ = ["SandboxPort"]
