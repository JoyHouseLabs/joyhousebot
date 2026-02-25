"""File system tools: read, write, edit."""

from pathlib import Path
from typing import Any

from joyhousebot.agent.tools.base import Tool
from joyhousebot.utils.exceptions import (
    ToolError,
    tool_error_handler,
)


_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def _resolve_path(path: str, allowed_dir: Path | None = None) -> Path:
    """Resolve path and optionally enforce directory restriction."""
    resolved = Path(path).expanduser().resolve()
    if allowed_dir:
        allowed_root = allowed_dir.expanduser().resolve()
        try:
            resolved.relative_to(allowed_root)
        except ValueError as e:
            raise PermissionError(
                f"Path {path} is outside allowed directory {allowed_root}"
            ) from e
    return resolved


def _validate_file_size(file_path: Path) -> None:
    """Check if file size is within limits."""
    try:
        size = file_path.stat().st_size
        if size > _MAX_FILE_SIZE:
            raise ToolError("read_file", f"File too large ({size} bytes). Maximum allowed: {_MAX_FILE_SIZE} bytes")
    except OSError:
        pass


class ReadFileTool(Tool):
    """Tool to read file contents."""

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read the contents of a file at the given path."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to read"
                }
            },
            "required": ["path"]
        }

    @tool_error_handler("Failed to read file")
    async def execute(self, path: str, **kwargs: Any) -> str:
        file_path = _resolve_path(path, self._allowed_dir)
        if not file_path.exists():
            raise ToolError(self.name, f"File not found: {path}")
        if not file_path.is_file():
            raise ToolError(self.name, f"Not a file: {path}")
        _validate_file_size(file_path)
        return file_path.read_text(encoding="utf-8")


class WriteFileTool(Tool):
    """Tool to write content to a file."""

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file at the given path. Creates parent directories if needed."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to write to"
                },
                "content": {
                    "type": "string",
                    "description": "The content to write"
                }
            },
            "required": ["path", "content"]
        }

    @tool_error_handler("Failed to write file")
    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        file_path = _resolve_path(path, self._allowed_dir)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} bytes to {path}"


class EditFileTool(Tool):
    """Tool to edit a file by replacing text."""

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return "Edit a file by replacing old_text with new_text. The old_text must exist exactly in the file."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to edit"
                },
                "old_text": {
                    "type": "string",
                    "description": "The exact text to find and replace"
                },
                "new_text": {
                    "type": "string",
                    "description": "The text to replace with"
                }
            },
            "required": ["path", "old_text", "new_text"]
        }

    @tool_error_handler("Failed to edit file")
    async def execute(self, path: str, old_text: str, new_text: str, **kwargs: Any) -> str:
        file_path = _resolve_path(path, self._allowed_dir)
        if not file_path.exists():
            raise ToolError(self.name, f"File not found: {path}")

        content = file_path.read_text(encoding="utf-8")

        if old_text not in content:
            raise ToolError(self.name, "old_text not found in file. Make sure it matches exactly.")

        count = content.count(old_text)
        if count > 1:
            raise ToolError(self.name, f"old_text appears {count} times. Please provide more context to make it unique.", is_recoverable=True)

        new_content = content.replace(old_text, new_text, 1)
        file_path.write_text(new_content, encoding="utf-8")

        return f"Successfully edited {path}"


class ListDirTool(Tool):
    """Tool to list directory contents."""

    def __init__(self, allowed_dir: Path | None = None):
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List the contents of a directory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The directory path to list"
                }
            },
            "required": ["path"]
        }

    @tool_error_handler("Failed to list directory")
    async def execute(self, path: str, **kwargs: Any) -> str:
        dir_path = _resolve_path(path, self._allowed_dir)
        if not dir_path.exists():
            raise ToolError(self.name, f"Directory not found: {path}")
        if not dir_path.is_dir():
            raise ToolError(self.name, f"Not a directory: {path}")

        items = []
        for item in sorted(dir_path.iterdir()):
            prefix = "[DIR] " if item.is_dir() else "[FILE] "
            items.append(f"{prefix}{item.name}")

        if not items:
            return f"Directory {path} is empty"

        return "\n".join(items)
