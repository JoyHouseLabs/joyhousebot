"""Shell execution tool.

Safety guard (allowlist + structured blocking):
- deny_patterns: dangerous commands/patterns (rm -rf, format, dd, redirect to raw device, fork bomb, etc.).
- allow_patterns: when non-empty, only commands matching allow_patterns are allowed (allowlist mode).
- restrict_to_workspace: path and working-dir checks; when True and shell_mode=False, shell metacharacters
  are forbidden so that the following are blocked in non-shell mode:
  - Redirection: >, >>, < (metachar pattern includes <, >).
  - Command substitution: $(...), `...` (pattern includes $, `).
  - Subshell: (...) (pattern includes ( and )).
  - Chaining: |, &&, ||, ; (pattern includes |, &, ;).
  When shell_mode=True, piping and redirects are allowed; guard relies on deny_patterns and path checks.
"""

import asyncio
import os
import re
import shlex
from pathlib import Path
from typing import Any, Callable

from joyhousebot.agent.tools.base import Tool


class ExecTool(Tool):
    """Tool to execute shell commands (direct or Docker backend with fallback)."""
    
    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        shell_mode: bool = False,
        container_enabled: bool = False,
        container_image: str = "alpine:3.18",
        container_workspace_mount: str = "",
        container_user: str = "",
        container_network: str = "none",
        get_skill_env: Callable[[str], dict[str, str]] | None = None,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.shell_mode = shell_mode
        self.container_enabled = container_enabled
        self.container_image = container_image or "alpine:3.18"
        self.container_workspace_mount = (container_workspace_mount or "").strip()
        self.container_user = (container_user or "").strip()
        self.container_network = container_network or "none"
        self.get_skill_env = get_skill_env
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",          # rm -r, rm -rf, rm -fr
            r"\bdel\s+/[fq]\b",              # del /f, del /q
            r"\brmdir\s+/s\b",               # rmdir /s
            r"\b(format|mkfs|diskpart)\b",   # disk operations
            r"\bdd\s+if=",                   # dd
            r">\s*/dev/sd",                  # write to disk
            r"\b(shutdown|reboot|poweroff)\b",  # system power
            r":\(\)\s*\{.*\};\s*:",          # fork bomb
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self._shell_metachar_pattern = re.compile(r"[|&;<>()`$]")
    
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
                "command": {
                    "type": "string",
                    "description": "The shell command to execute"
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command"
                }
            },
            "required": ["command"]
        }
    
    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error
        
        if self.container_enabled:
            result, fallback_reason = await self._execute_docker_or_fallback(command, cwd)
            if fallback_reason:
                result = (result or "(no output)").rstrip() + f"\n[Sandbox fallback: {fallback_reason}]"
            return result
        
        return await self._execute_direct(command, cwd)
    
    async def _execute_docker_or_fallback(self, command: str, cwd: str) -> tuple[str, str | None]:
        """Try Docker backend; on failure fall back to direct and return (output, fallback_reason)."""
        from joyhousebot.sandbox.docker_backend import is_docker_available, run_in_container
        
        if not await is_docker_available():
            out = await self._execute_direct(command, cwd)
            return out, "Docker unavailable; ran in host"
        
        workspace_host = self.container_workspace_mount or cwd
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
        )
        if err is None:
            # Success
            if exit_code != 0:
                out = (out or "").rstrip() + f"\nExit code: {exit_code}"
            return (out or "(no output)").rstrip(), None
        # Docker failed: fallback to direct
        try:
            direct_out = await self._execute_direct(command, cwd)
            return direct_out, f"Docker failed ({err}); ran in host"
        except Exception as e:
            return f"Error: {e}\n[Docker had failed: {err}]", "Docker failed then direct failed"
    
    def _build_env_for_cwd(self, cwd: str) -> dict[str, str]:
        """Build environment for subprocess: current env + per-skill env when cwd is under workspace/skills/<name>."""
        env = dict(os.environ)
        if self.get_skill_env:
            extra = self.get_skill_env(cwd)
            if extra:
                env.update(extra)
        return env

    async def _execute_direct(self, command: str, cwd: str) -> str:
        """Run command on host (current behavior)."""
        run_env = self._build_env_for_cwd(cwd)
        try:
            if self.shell_mode:
                # Run via shell so piping (|), redirects (>), chaining (;) work (OpenClaw-aligned).
                shell = os.environ.get("SHELL", "/bin/sh")
                if shell.endswith("fish"):
                    shell = "/bin/sh"
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=run_env,
                    executable=shell,
                )
            else:
                argv = shlex.split(command, posix=True)
                if not argv:
                    return "Error: Empty command"
                process = await asyncio.create_subprocess_exec(
                    argv[0],
                    *argv[1:],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=run_env,
                )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                return f"Error: Command timed out after {self.timeout} seconds"
            
            output_parts = []
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))
            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")
            if process.returncode != 0:
                output_parts.append(f"\nExit code: {process.returncode}")
            result = "\n".join(output_parts) if output_parts else "(no output)"
            max_len = 10000
            if len(result) > max_len:
                result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"
            return result
        except Exception as e:
            return f"Error executing command: {str(e)}"

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard: deny_patterns, allowlist (allow_patterns), and when restrict_to_workspace
        and not shell_mode, block shell metacharacters (| & ; < > ( ) ` $) so redirects, command substitution,
        subshells, and chaining are rejected. Path traversal and paths outside working_dir are also blocked."""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace and not self.shell_mode:
            if self._shell_metachar_pattern.search(cmd):
                return "Error: Command blocked by safety guard (shell metacharacters are not allowed)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).expanduser().resolve()
            if self.working_dir:
                allowed_root = Path(self.working_dir).expanduser().resolve()
            else:
                allowed_root = cwd_path

            try:
                cwd_path.relative_to(allowed_root)
            except ValueError:
                return "Error: Command blocked by safety guard (working_dir outside allowed root)"

            win_paths = re.findall(r"[A-Za-z]:\\[^\\\"']+", cmd)
            # Only match absolute paths — avoid false positives on relative
            # paths like ".venv/bin/python" where "/bin/python" would be
            # incorrectly extracted by the old pattern.
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
                        return "Error: Command blocked by safety guard (path outside working dir)"

        return None

