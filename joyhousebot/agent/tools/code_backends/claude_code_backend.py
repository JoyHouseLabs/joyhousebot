"""Claude Code CLI backend for the code_runner tool."""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from joyhousebot.agent.tools.code_backends.base import CodeBackend, RunResult

# Extra PATH entries so we find `claude` when run from non-interactive env (e.g. uv run, IDE)
# Same as typical bash/zsh for macOS/Linux
_EXTRA_PATH = os.pathsep.join([
    "/opt/homebrew/bin",
    "/usr/local/bin",
    os.path.expanduser("~/.local/bin"),
    os.path.expanduser("~/bin"),
])


def _resolve_claude_command(command: str) -> str:
    """Resolve to full path so subprocess finds claude even when PATH is minimal."""
    cmd = (command or "claude").strip()
    if os.path.isabs(cmd) and os.path.isfile(cmd):
        return cmd
    env_path = os.environ.get("PATH", "") + os.pathsep + _EXTRA_PATH
    found = shutil.which(cmd, path=env_path)
    return found if found else cmd


def _escape_for_shell(s: str) -> str:
    """Escape prompt for safe use in sh -c '...' (single-quoted)."""
    return s.replace("'", "'\"'\"'")


class ClaudeCodeBackend(CodeBackend):
    """Run Claude Code CLI (e.g. `claude -p \"prompt\"`) in host or container."""

    def __init__(
        self,
        command: str = "claude",
        timeout_default: int = 300,
    ):
        self._command = (command or "claude").strip()
        self._timeout_default = max(60, timeout_default)

    @property
    def backend_id(self) -> str:
        return "claude_code"

    async def run(
        self,
        prompt: str,
        working_dir: str | None = None,
        timeout: int = 300,
        mode: str = "host",
        *,
        container_image: str = "",
        container_workspace_mount: str = "",
        container_user: str = "",
        container_network: str = "none",
        output_callback: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> RunResult:
        effective_timeout = max(60, timeout) if timeout else self._timeout_default
        cwd = (working_dir or os.getcwd()).strip() or os.getcwd()
        # Resolve to full path so we find claude when run from non-interactive env (PATH may be minimal)
        claude_bin = _resolve_claude_command(self._command)
        safe_prompt = _escape_for_shell(prompt.strip() or "(no prompt)")
        cmd = f"{claude_bin} -p '{safe_prompt}'"

        if mode == "container":
            return await self._run_container(cmd, cwd, effective_timeout, container_image, container_workspace_mount, container_user, container_network)
        if mode == "auto":
            result = await self._run_container(cmd, cwd, effective_timeout, container_image, container_workspace_mount, container_user, container_network)
            if result.error_message and ("not found" in result.error_message.lower() or "unavailable" in result.error_message.lower() or "timeout" in result.error_message.lower() or "docker" in result.error_message.lower()):
                host_result = await self._run_host(cmd, cwd, effective_timeout, output_callback=output_callback)
                host_result.fallback_used = True
                return host_result
            return result
        return await self._run_host(cmd, cwd, effective_timeout, output_callback=output_callback)

    async def _run_host(
        self,
        command: str,
        cwd: str,
        timeout_seconds: int,
        output_callback: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> RunResult:
        """Execute CLI on host via shell. Ensures HOME is set so ~/.claude is found."""
        shell = os.environ.get("SHELL", "/bin/sh")
        if shell.endswith("fish"):
            shell = "/bin/sh"
        env = dict(os.environ)
        if "HOME" not in env or not env["HOME"].strip():
            env["HOME"] = os.path.expanduser("~")
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                executable=shell,
                env=env,
            )
            out_buf: list[str] = []
            err_buf: list[str] = []

            async def read_stream(
                stream: asyncio.StreamReader | None,
                stream_name: str,
                buf: list[str],
            ) -> None:
                if stream is None:
                    return
                while True:
                    try:
                        chunk = await stream.read(4096)
                        if not chunk:
                            break
                        text = chunk.decode("utf-8", errors="replace")
                        buf.append(text)
                        if output_callback:
                            await output_callback(stream_name, text)
                    except (asyncio.CancelledError, BrokenPipeError):
                        break

            if output_callback is not None:
                task_out = asyncio.create_task(read_stream(proc.stdout, "stdout", out_buf))
                task_err = asyncio.create_task(read_stream(proc.stderr, "stderr", err_buf))
                try:
                    await asyncio.wait_for(proc.wait(), timeout=float(timeout_seconds))
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    task_out.cancel()
                    task_err.cancel()
                    try:
                        await task_out
                    except asyncio.CancelledError:
                        pass
                    try:
                        await task_err
                    except asyncio.CancelledError:
                        pass
                    return RunResult(
                        backend_id=self.backend_id,
                        mode="host",
                        success=False,
                        exit_code=-1,
                        stdout="".join(out_buf),
                        stderr="".join(err_buf),
                        error_message=f"Command timed out after {timeout_seconds} seconds. Install Claude Code CLI and ensure ANTHROPIC_API_KEY or ~/.claude credentials are set.",
                    )
                await asyncio.gather(task_out, task_err)
                out = "".join(out_buf)
                err = "".join(err_buf)
            else:
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=float(timeout_seconds))
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    return RunResult(
                        backend_id=self.backend_id,
                        mode="host",
                        success=False,
                        exit_code=-1,
                        stdout="",
                        stderr="",
                        error_message=f"Command timed out after {timeout_seconds} seconds. Install Claude Code CLI and ensure ANTHROPIC_API_KEY or ~/.claude credentials are set.",
                    )
                out = stdout.decode("utf-8", errors="replace")
                err = stderr.decode("utf-8", errors="replace")

            code = proc.returncode if proc.returncode is not None else -1
            if code != 0 and not out and not err:
                return RunResult(
                    backend_id=self.backend_id,
                    mode="host",
                    success=False,
                    exit_code=code,
                    stdout=out,
                    stderr=err,
                    error_message="Claude Code CLI failed. Check that 'claude' is installed (npm install -g @anthropic-ai/claude-code) and ANTHROPIC_API_KEY or ~/.claude/.credentials.json is set.",
                )
            return RunResult(
                backend_id=self.backend_id,
                mode="host",
                success=(code == 0),
                exit_code=code,
                stdout=out,
                stderr=err,
            )
        except FileNotFoundError:
            return RunResult(
                backend_id=self.backend_id,
                mode="host",
                success=False,
                exit_code=-1,
                stdout="",
                stderr="",
                error_message=f"Claude Code CLI not found. Install with: npm install -g @anthropic-ai/claude-code. Expected command: {self._command}",
            )
        except Exception as e:
            return RunResult(
                backend_id=self.backend_id,
                mode="host",
                success=False,
                exit_code=-1,
                stdout="",
                stderr="",
                error_message=str(e),
            )

    async def _run_container(
        self,
        command: str,
        cwd: str,
        timeout_seconds: int,
        image: str,
        workspace_mount: str,
        user: str,
        network: str,
    ) -> RunResult:
        """Execute CLI inside a one-off container. Requires image with Claude Code installed."""
        from joyhousebot.sandbox.docker_backend import is_docker_available, run_in_container

        if not image or not image.strip():
            return RunResult(
                backend_id=self.backend_id,
                mode="container",
                success=False,
                exit_code=-1,
                stdout="",
                stderr="",
                error_message="Container image not configured for code_runner. Set tools.code_runner.container_image (e.g. ghcr.io/13rac1/openclaw-claude-code:latest).",
            )
        if not await is_docker_available():
            return RunResult(
                backend_id=self.backend_id,
                mode="container",
                success=False,
                exit_code=-1,
                stdout="",
                stderr="",
                error_message="Docker unavailable; run in host or install Docker.",
            )
        host_workspace = (workspace_mount or cwd).strip() or cwd
        host_path = Path(host_workspace).expanduser().resolve()
        if not host_path.exists():
            return RunResult(
                backend_id=self.backend_id,
                mode="container",
                success=False,
                exit_code=-1,
                stdout="",
                stderr="",
                error_message=f"Workspace path does not exist: {host_path}",
            )
        out, exit_code, err = await run_in_container(
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            image=image,
            workspace_host_path=str(host_path),
            workspace_container_path="/workspace",
            user=user or "",
            network=network or "none",
            shell_mode=True,
        )
        if err is not None:
            return RunResult(
                backend_id=self.backend_id,
                mode="container",
                success=False,
                exit_code=exit_code if exit_code >= 0 else -1,
                stdout=out or "",
                stderr=err[:2000] if len(err) > 2000 else err,
                error_message=err,
            )
        return RunResult(
            backend_id=self.backend_id,
            mode="container",
            success=(exit_code == 0),
            exit_code=exit_code,
            stdout=out or "",
            stderr="",
        )
