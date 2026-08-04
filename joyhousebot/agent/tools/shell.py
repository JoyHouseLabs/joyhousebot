"""Shell execution tool.

Safety guard (allowlist + structured blocking):
- deny_patterns: dangerous commands/patterns (rm -rf, format, dd, redirect to raw device, fork bomb, etc.).
  NOTE: deny_patterns/allow_patterns are a best-effort UX backstop only. The real security
  boundary is the container sandbox (resource limits, dropped caps, no network by default);
  never rely on the regex guard alone for isolation.
- allow_patterns: when non-empty, only commands matching allow_patterns are allowed (allowlist mode).
- restrict_to_workspace: path and working-dir checks; when True and shell_mode=False, shell metacharacters
  are forbidden so that the following are blocked in non-shell mode:
  - Redirection: >, >>, < (metachar pattern includes <, >).
  - Command substitution: $(...), `...` (pattern includes $, `).
  - Subshell: (...) (pattern includes ( and )).
  - Chaining: |, &&, ||, ; (pattern includes |, &, ;).
  - Embedded newlines (\\n, \\r), which could smuggle extra commands past line-based checks.
  When shell_mode=True, piping and redirects are allowed; guard relies on deny_patterns and path checks.
"""

import asyncio
import hashlib
import re
import shutil
import time
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.agent.tools.base import Tool
from joyhousebot.capabilities.tool_adapter import ToolInvocationError
from joyhousebot.utils.exceptions import (
    sanitize_error_message,
)

_SCRATCH_MAX_AGE_SECONDS = 24 * 3600


def _cleanup_old_scratch(scratch_root: Path) -> None:
    """Opportunistically drop run scratch dirs older than 24h; failures are logged only."""
    try:
        cutoff = time.time() - _SCRATCH_MAX_AGE_SECONDS
        for child in scratch_root.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                continue
    except OSError as e:
        logger.warning("scratch cleanup failed for {}: {}", scratch_root, e)


class ExecTool(Tool):
    """Tool to execute shell commands directly or in a fail-closed container."""

    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = True,
        shell_mode: bool = False,
        container_image: str = "alpine:3.18",
        container_workspace_mount: str = "",
        container_user: str = "65534:65534",
        container_network: str = "none",
        container_memory: str = "512m",
        container_cpus: str = "1",
        container_pids_limit: int = 256,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.shell_mode = shell_mode
        self.container_image = container_image or "alpine:3.18"
        self.container_workspace_mount = (container_workspace_mount or "").strip()
        self.container_user = (container_user or "").strip()
        self.container_network = (container_network or "none").strip()
        if self.container_network == "host":
            logger.warning(
                "exec container_network='host' is not allowed on the cloud platform; "
                "falling back to 'none'"
            )
            self.container_network = "none"
        self.container_memory = container_memory or "512m"
        self.container_cpus = container_cpus or "1"
        self.container_pids_limit = int(container_pids_limit or 256)
        self._mount_config_error: str | None = None
        if not restrict_to_workspace:
            # Unrestricted exec requires an explicit mount source; never fall back to
            # (or accept) the platform process working directory as the mount source.
            mount = self.container_workspace_mount
            try:
                mount_is_cwd = bool(mount) and (
                    Path(mount).expanduser().resolve() == Path.cwd().resolve()
                )
            except OSError:
                mount_is_cwd = True
            if not mount or mount_is_cwd:
                self._mount_config_error = (
                    "container_workspace_mount must be explicitly configured when "
                    "restrict_to_workspace is False, and must not be the platform "
                    "process working directory"
                )
                logger.warning("exec tool misconfigured: {}", self._mount_config_error)
        self.restrict_to_workspace = restrict_to_workspace
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",
            r"\bdel\s+/[fq]\b",
            r"\brmdir\s+/s\b",
            r"\b(format|mkfs|diskpart)\b",
            r"\bdd\s+if=",
            r">\s*/dev/sd",
            r"\b(shutdown|reboot|poweroff)\b",
            r":\(\)\s*\{.*\};\s*:",
        ]
        self.allow_patterns = allow_patterns or []
        self._shell_metachar_pattern = re.compile(r"[|&;<>()`$\n\r]")

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Execute a shell command and return its output. Use with caution."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command",
                },
            },
            "required": ["command"],
        }

    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        if self._mount_config_error:
            raise ToolInvocationError("SANDBOX_MISCONFIGURED", self._mount_config_error)
        cwd = working_dir or self.working_dir or str(Path.cwd())
        cwd = self._scoped_working_dir(cwd, kwargs.get("tool_context"))
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            raise ToolInvocationError("COMMAND_BLOCKED", guard_error)

        return await self._execute_docker(command, cwd)

    def _scoped_working_dir(self, cwd: str, tool_context: Any) -> str:
        """Give each root workflow a private local scratch mount."""
        if not self.restrict_to_workspace or tool_context is None or not self.working_dir:
            return cwd
        from joyhousebot.runtime.context import ToolExecutionContext

        if not isinstance(tool_context, ToolExecutionContext):
            return cwd
        workflow_id = tool_context.root_run_id or tool_context.run_id
        scope = hashlib.sha256(
            f"{tool_context.user_id}\0{tool_context.agent_id}\0{workflow_id}".encode()
        ).hexdigest()[:24]
        scratch_root = Path(self.working_dir).expanduser().resolve() / ".scratch"
        root = scratch_root / scope
        root.mkdir(parents=True, exist_ok=True)
        # The container runs as nobody (65534) by default; keep the scratch mount writable.
        try:
            root.chmod(0o777)
        except OSError as e:
            logger.warning("failed to chmod scratch dir {}: {}", root, e)
        _cleanup_old_scratch(scratch_root)
        requested = Path(cwd).expanduser()
        if requested.is_absolute():
            workspace = Path(self.working_dir).expanduser().resolve()
            try:
                relative = requested.resolve().relative_to(workspace)
            except ValueError:
                return str(requested.resolve())
            return str((root / relative).resolve())
        return str((root / requested).resolve())

    async def _execute_docker(self, command: str, cwd: str) -> str:
        """Run in Docker and fail closed; never downgrade to host execution."""
        from joyhousebot.sandbox.docker_backend import is_docker_available, run_in_container

        try:
            if not await is_docker_available():
                raise ToolInvocationError("SANDBOX_UNAVAILABLE", "execution sandbox is unavailable", retryable=True)
        except Exception as e:
            if isinstance(e, ToolInvocationError):
                raise
            raise ToolInvocationError(
                "SANDBOX_CHECK_FAILED", sanitize_error_message(str(e)), retryable=True
            ) from e

        workspace_host = (
            cwd if self.restrict_to_workspace else (self.container_workspace_mount or cwd)
        )
        try:
            out, exit_code, err = await run_in_container(
                command=command,
                cwd=cwd,
                timeout_seconds=self.timeout,
                image=self.container_image,
                workspace_host_path=workspace_host,
                workspace_container_path="/workspace",
                user=self.container_user,
                network=self.container_network,
                shell_mode=self.shell_mode,
                memory=self.container_memory,
                cpus=self.container_cpus,
                pids_limit=self.container_pids_limit,
            )
            if err is None:
                if exit_code != 0:
                    out = (out or "").rstrip() + f"\nExit code: {exit_code}"
                return (out or "(no output)").rstrip()
            raise ToolInvocationError("SANDBOX_EXECUTION_FAILED", sanitize_error_message(str(err)))
        except asyncio.TimeoutError:
            raise ToolInvocationError(
                "COMMAND_TIMEOUT", f"Command timed out after {self.timeout} seconds", retryable=True
            )
        except ToolInvocationError:
            raise
        except Exception as e:
            raise ToolInvocationError("SANDBOX_EXECUTION_FAILED", sanitize_error_message(str(e))) from e

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort UX backstop only: deny_patterns, allowlist (allow_patterns), and when
        restrict_to_workspace and not shell_mode, block shell metacharacters (| & ; < > ( ) ` $
        and embedded newlines) so redirects, command substitution, subshells, and chaining are
        rejected. Path traversal and paths outside working_dir are also blocked. The real
        security boundary is the container sandbox, not this regex guard."""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace and not self.shell_mode:
            if self._shell_metachar_pattern.search(cmd):
                return (
                    "Command blocked by safety guard (shell metacharacters are not allowed)"
                )

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).expanduser().resolve()
            if self.working_dir:
                allowed_root = Path(self.working_dir).expanduser().resolve()
            else:
                allowed_root = cwd_path

            try:
                cwd_path.relative_to(allowed_root)
            except ValueError:
                return "Command blocked by safety guard (working_dir outside allowed root)"

            win_paths = re.findall(r"[A-Za-z]:\\[^\\\"']+", cmd)
            posix_paths = re.findall(r"(?:^|[\s|>])(/[^\s\"'>]+)", cmd)

            for raw in win_paths + posix_paths:
                try:
                    p = Path(raw.strip()).expanduser().resolve()
                except Exception:
                    continue
                if p.is_absolute():
                    try:
                        p.relative_to(allowed_root)
                    except ValueError:
                        return "Command blocked by safety guard (path outside working dir)"

        return None
