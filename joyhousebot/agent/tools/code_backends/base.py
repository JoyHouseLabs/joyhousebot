"""Abstract interface and return contract for code backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class RunResult:
    """
    Unified result from a code backend run.
    Tool layer formats this into a string for the agent (backend, mode, exit_code, stderr summary).
    """

    backend_id: str
    mode: str  # "host" | "container"
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    error_message: str | None = None  # e.g. "CLI not found", "timeout", "auth required"
    fallback_used: bool = False  # True when auto fell back from container to host

    def to_display_string(self, max_stdout: int = 10000, max_stderr: int = 2000) -> str:
        """Format for agent consumption: backend, mode, exit_code, stderr summary, then stdout."""
        parts = [
            f"[backend={self.backend_id} mode={self.mode} exit_code={self.exit_code}]",
        ]
        if self.fallback_used:
            parts.append("(Fallback: ran in host after container was unavailable)")
        if self.error_message:
            parts.append(f"Error: {self.error_message}")
        if self.stderr and self.stderr.strip():
            stderr_preview = self.stderr.strip()
            if len(stderr_preview) > max_stderr:
                stderr_preview = stderr_preview[:max_stderr] + "... (truncated)"
            parts.append(f"STDERR:\n{stderr_preview}")
        out = self.stdout or "(no output)"
        if len(out) > max_stdout:
            out = out[:max_stdout] + f"\n... (truncated, {len(self.stdout) - max_stdout} more chars)"
        parts.append(out)
        if self.exit_code != 0 and "Exit code" not in out:
            parts.append(f"\nExit code: {self.exit_code}")
        return "\n".join(parts)


class CodeBackend(ABC):
    """
    Adapter for a coding CLI (Claude Code, Codex, OpenCode, etc.).
    First phase: run(prompt, working_dir, timeout, mode) returning RunResult.
    Later: start/status/output/cancel for async jobs if needed.
    """

    @property
    @abstractmethod
    def backend_id(self) -> str:
        """Unique backend identifier, e.g. 'claude_code'."""
        pass

    @abstractmethod
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
        """
        Run a single coding task (sync wait for completion).
        mode: "host" | "container" | "auto" (auto = try container, fallback host).
        output_callback: If set, called with (stream_name, text_chunk) for real-time stdout/stderr.
        Returns RunResult with backend_id, mode actually used, exit_code, stdout, stderr, error_message.
        """
        pass
