"""Unified code_runner tool: delegates to pluggable backends (Claude Code, Codex, OpenCode, etc.)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from joyhousebot.agent.tools.base import Tool
from joyhousebot.agent.tools.code_backends.base import CodeBackend, RunResult
from joyhousebot.agent.tools.code_backends.claude_code_backend import ClaudeCodeBackend
from joyhousebot.utils.exceptions import (
    ToolError,
    TimeoutError,
    ValidationError,
    ErrorCategory,
    sanitize_error_message,
    classify_exception,
)

DEFAULT_APPROVAL_TIMEOUT_MS = 120_000


def _get_backend(backend_id: str, claude_command: str = "claude", timeout_default: int = 300) -> CodeBackend | None:
    """Resolve backend by id. Extend here for codex, opencode, etc."""
    if backend_id == "claude_code":
        return ClaudeCodeBackend(command=claude_command, timeout_default=timeout_default)
    return None


class CodeRunnerTool(Tool):
    """Run a coding task via a configurable backend (e.g. Claude Code CLI) in host or container."""

    def __init__(
        self,
        working_dir: str | None = None,
        default_backend: str = "claude_code",
        default_mode: str = "auto",
        timeout: int = 300,
        claude_code_command: str = "claude",
        container_image: str = "",
        container_workspace_mount: str = "",
        container_user: str = "",
        container_network: str = "none",
        require_approval: bool = False,
        approval_request_fn: Callable[..., Awaitable[str | None]] | None = None,
    ):
        self._working_dir = working_dir
        self._default_backend = (default_backend or "claude_code").strip().lower()
        self._default_mode = (default_mode or "auto").strip().lower()
        if self._default_mode not in ("host", "container", "auto"):
            self._default_mode = "auto"
        self._timeout = max(60, timeout)
        self._claude_code_command = (claude_code_command or "claude").strip()
        self._container_image = (container_image or "").strip()
        self._container_workspace_mount = (container_workspace_mount or "").strip()
        self._container_user = (container_user or "").strip()
        self._container_network = (container_network or "none").strip()
        self._require_approval = bool(require_approval)
        self._approval_request_fn = approval_request_fn

    @property
    def name(self) -> str:
        return "code_runner"

    @property
    def description(self) -> str:
        return (
            "Run a coding task using a backend (e.g. Claude Code). Use for multi-step code edits, "
            "refactors, or running tests. Pass prompt and optional working_dir, backend, mode (host|container|auto), timeout."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The coding task or prompt to send to the backend (e.g. 'Add a unit test for function X')",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the task; defaults to agent workspace",
                },
                "backend": {
                    "type": "string",
                    "description": "Backend to use",
                    "enum": ["claude_code"],
                },
                "mode": {
                    "type": "string",
                    "description": "Execution mode: host (run on machine), container (isolated), auto (container with host fallback)",
                    "enum": ["host", "container", "auto"],
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default from config, max 600)",
                    "minimum": 60,
                    "maximum": 600,
                },
            },
            "required": ["prompt"],
        }

    async def execute(
        self,
        prompt: str,
        working_dir: str | None = None,
        backend: str | None = None,
        mode: str | None = None,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> str:
        execution_stream_callback = kwargs.pop("execution_stream_callback", None)

        prompt = (prompt or "").strip()
        if not prompt:
            raise ValidationError("prompt is required", field="prompt")

        backend_id = (backend or self._default_backend).strip().lower()
        mode_val = (mode or self._default_mode).strip().lower()
        if mode_val not in ("host", "container", "auto"):
            mode_val = self._default_mode
        effective_timeout = self._timeout
        if timeout is not None and 60 <= timeout <= 600:
            effective_timeout = timeout
        cwd = (working_dir or self._working_dir or "").strip() or None

        if self._require_approval and self._approval_request_fn:
            command_preview = (f"code_runner: {(prompt or '').strip()[:200]}" or "code_runner: (no prompt)").strip()
            request_id = f"cr_{uuid.uuid4().hex[:12]}"
            logger.info(
                "Code runner approval required (id {}). Approve with: joyhousebot approvals resolve {} allow-once",
                request_id,
                request_id,
            )
            decision: str | None = await self._approval_request_fn(
                command_preview, DEFAULT_APPROVAL_TIMEOUT_MS, request_id
            )
            if decision not in ("allow-once", "allow-always"):
                if decision == "deny":
                    return "Error: Approval denied."
                return f"Error: Approval expired or not granted. To approve, run: joyhousebot approvals resolve {request_id} allow-once"

        b = _get_backend(backend_id, claude_command=self._claude_code_command, timeout_default=self._timeout)
        if not b:
            raise ValidationError(f"Unknown backend '{backend_id}'. Supported: claude_code.", field="backend")

        output_callback = None
        if execution_stream_callback:

            async def _output_cb(stream_name: str, text: str) -> None:
                await execution_stream_callback(
                    "tool_output",
                    {"tool": "code_runner", "stream": stream_name, "text": text},
                )

            output_callback = _output_cb

        try:
            result = await b.run(
                prompt=prompt,
                working_dir=cwd,
                timeout=effective_timeout,
                mode=mode_val,
                container_image=self._container_image,
                container_workspace_mount=self._container_workspace_mount or cwd or "",
                container_user=self._container_user,
                container_network=self._container_network,
                output_callback=output_callback,
            )
            return result.to_display_string()
        except asyncio.TimeoutError:
            raise TimeoutError("code_runner", effective_timeout)
        except FileNotFoundError as e:
            raise ToolError(self.name, f"Working directory not found: {cwd}")
        except PermissionError as e:
            raise ToolError(self.name, f"Permission denied: {sanitize_error_message(str(e))}")
        except Exception as e:
            code, category, _ = classify_exception(e)
            sanitized = sanitize_error_message(str(e))
            logger.error(f"Code runner error [{code}]: {sanitized}")
            raise ToolError(self.name, sanitized, is_recoverable=(category == ErrorCategory.RECOVERABLE))
