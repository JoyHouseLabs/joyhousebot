"""memory_get tool: read memory file by path with optional line range (OpenClaw-aligned). Returns empty text if file missing."""

import json
from pathlib import Path
from typing import Any

from joyhousebot.agent.tools.base import Tool
from joyhousebot.agent.memory import safe_scope_key


class MemoryGetTool(Tool):
    """Read a file under workspace/memory/ by path. Returns { text, path }; text is empty if file missing.
    When memory scope is set, only paths under memory/<scope_key>/ are allowed."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self._memory_scope_key: str | None = None
        self._update_memory_dir()

    def _update_memory_dir(self) -> None:
        base = self.workspace / "memory"
        if self._memory_scope_key:
            safe = safe_scope_key(self._memory_scope_key)
            self.memory_dir = base / safe if safe else base
        else:
            self.memory_dir = base

    def set_memory_scope(self, scope_key: str | None) -> None:
        """Set current memory scope (per-session/per-user); restricts reads to memory/<scope_key>/."""
        self._memory_scope_key = scope_key
        self._update_memory_dir()

    @property
    def name(self) -> str:
        return "memory_get"

    @property
    def description(self) -> str:
        return (
            "Read a memory file by path (under memory/). Use for MEMORY.md, daily logs (memory/YYYY-MM-DD.md), "
            "or memory/insights/*.md, memory/lessons/*.md. Returns { text, path }; text is empty if file does not exist."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative path, e.g. memory/MEMORY.md or memory/2026-02-25.md",
                },
                "start_line": {
                    "type": "integer",
                    "description": "Optional 1-based start line (inclusive)",
                    "minimum": 1,
                },
                "num_lines": {
                    "type": "integer",
                    "description": "Optional number of lines to return from start_line",
                    "minimum": 1,
                },
            },
            "required": ["path"],
        }

    def _resolve_safe(self, path: str) -> Path | None:
        """Resolve path under workspace/memory/ (or memory/<scope_key>/ when scoped); return None if outside."""
        path_str = (path or "").strip().lstrip("/")
        if not path_str or ".." in path_str:
            return None
        if path_str.startswith("memory/"):
            path_str = path_str[7:].lstrip("/")
        if self._memory_scope_key:
            safe = safe_scope_key(self._memory_scope_key)
            if safe and path_str.startswith(safe + "/"):
                path_str = path_str[len(safe) + 1:]
            elif safe and path_str == safe:
                return None
        resolved = (self.memory_dir / path_str).resolve()
        try:
            resolved.relative_to(self.memory_dir.resolve())
        except ValueError:
            return None
        if path_str.startswith("..") or ".." in path_str:
            return None
        return resolved

    async def execute(
        self,
        path: str,
        start_line: int | None = None,
        num_lines: int | None = None,
        **kwargs: Any,
    ) -> str:
        resolved = self._resolve_safe(path)
        if not resolved:
            return json.dumps({"text": "", "path": path, "error": "path must be under memory/"})
        if not resolved.exists() or not resolved.is_file():
            return json.dumps({"text": "", "path": path})
        try:
            text = resolved.read_text(encoding="utf-8")
        except Exception:
            return json.dumps({"text": "", "path": path})
        if start_line is not None and num_lines is not None and start_line >= 1 and num_lines >= 1:
            lines = text.splitlines()
            start = min(start_line - 1, len(lines))
            end = min(start + num_lines, len(lines))
            text = "\n".join(lines[start:end])
        return json.dumps({"text": text, "path": path})
