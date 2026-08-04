"""Read durable memory documents from the current run scope."""

from __future__ import annotations

import json
from typing import Any

from joyhousebot.agent.memory import MemoryStore
from joyhousebot.agent.memory_policy import EffectiveMemoryPolicy
from joyhousebot.agent.tools.base import Tool
from joyhousebot.capabilities.tool_adapter import ToolInvocationError
from joyhousebot.runtime.context import ToolExecutionContext


class MemoryGetTool(Tool):
    def __init__(self, runtime_store: Any) -> None:
        if runtime_store is None:
            raise ValueError("MemoryGetTool requires a durable runtime_store")
        self.runtime_store = runtime_store

    @property
    def name(self) -> str:
        return "memory_get"

    @property
    def description(self) -> str:
        return "Read a document from the current user's durable Agent memory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Logical path such as memory/MEMORY.md or memory/2026-02-25.md",
                },
                "start_line": {"type": "integer", "minimum": 1},
                "num_lines": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
        }

    @staticmethod
    def _relative_path(path: str) -> str:
        clean = str(path or "").strip().replace("\\", "/").lstrip("/")
        if clean.startswith("memory/"):
            clean = clean[7:]
        if not clean or any(part in {"", ".", ".."} for part in clean.split("/")):
            return ""
        return clean

    async def execute(
        self,
        path: str,
        start_line: int | None = None,
        num_lines: int | None = None,
        **kwargs: Any,
    ) -> str:
        context = kwargs.get("tool_context")
        if not isinstance(context, ToolExecutionContext) or not context.memory_scope:
            raise ToolInvocationError("CONTEXT_REQUIRED", "run memory scope is required")
        relative = self._relative_path(path)
        if not relative:
            raise ToolInvocationError("INVALID_PARAMETERS", "invalid memory path")
        policy = EffectiveMemoryPolicy.from_dict(context.memory_policy)
        if not policy.allows_path(relative, "read"):
            raise ToolInvocationError(
                "MEMORY_ACCESS_DENIED",
                "this Agent memory layer is disabled by its memory policy",
            )
        text = MemoryStore(self.runtime_store, context.memory_scope).read_relative(relative)
        if start_line is not None and num_lines is not None:
            lines = text.splitlines()
            start = min(start_line - 1, len(lines))
            text = "\n".join(lines[start : start + num_lines])
        return json.dumps({"text": text, "path": path})
