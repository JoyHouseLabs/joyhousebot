"""Run-scoped scratch file tools plus durable memory document access."""

import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.agent.memory_policy import EffectiveMemoryPolicy
from joyhousebot.agent.tools.base import Tool
from joyhousebot.runtime.context import ToolExecutionContext
from joyhousebot.utils.exceptions import (
    ToolError,
    tool_error_handler,
)

_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

_SCRATCH_MAX_AGE_SECONDS = 24 * 3600


def _warn_if_unrestricted(tool_name: str, allowed_dir: Path | None) -> None:
    if allowed_dir is None:
        logger.warning(
            "{} registered without allowed_dir; file access is not restricted to a workspace",
            tool_name,
        )


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


def _atomic_write_text(file_path: Path, content: str) -> None:
    """Write via a temp file in the same directory, then atomically replace the target."""
    fd, tmp = tempfile.mkstemp(
        dir=str(file_path.parent), prefix=file_path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, file_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _resolve_path(
    path: str,
    allowed_dir: Path | None = None,
    *,
    workspace: Path | None = None,
    tool_context: Any = None,
) -> Path:
    """Resolve paths into an isolated workflow scratch root when run-scoped."""
    requested = Path(path).expanduser()
    allowed_root = allowed_dir.expanduser().resolve() if allowed_dir else None
    if (
        allowed_root is not None
        and workspace is not None
        and isinstance(tool_context, ToolExecutionContext)
    ):
        workspace_root = workspace.expanduser().resolve()
        memory_root = (workspace_root / "memory").resolve()
        absolute = requested.resolve() if requested.is_absolute() else None
        if absolute is None and requested.parts and requested.parts[0] == "memory":
            allowed_root = memory_root
            absolute = (workspace_root / requested).resolve()
        if absolute is not None:
            try:
                absolute.relative_to(memory_root)
                return absolute
            except ValueError:
                pass
        workflow_id = tool_context.root_run_id or tool_context.run_id
        scope = hashlib.sha256(
            f"{tool_context.user_id}\0{tool_context.agent_id}\0{workflow_id}".encode()
        ).hexdigest()[:24]
        allowed_root = (workspace_root / ".scratch" / scope).resolve()
        allowed_root.mkdir(parents=True, exist_ok=True)
        _cleanup_old_scratch(allowed_root.parent)
        if absolute is not None:
            try:
                relative = absolute.relative_to(workspace_root)
            except ValueError:
                resolved = absolute
            else:
                resolved = (allowed_root / relative).resolve()
        else:
            resolved = (allowed_root / requested).resolve()
    else:
        resolved = requested.resolve()
    if allowed_root is not None:
        try:
            resolved.relative_to(allowed_root)
        except ValueError as e:
            raise PermissionError(f"Path {path} is outside allowed directory {allowed_root}") from e
    return resolved


def _validate_file_size(file_path: Path) -> None:
    """Check if file size is within limits."""
    try:
        size = file_path.stat().st_size
        if size > _MAX_FILE_SIZE:
            raise ToolError(
                "read_file",
                f"File too large ({size} bytes). Maximum allowed: {_MAX_FILE_SIZE} bytes",
            )
    except OSError:
        pass


def _scoped_memory_store(
    file_path: Path,
    *,
    workspace: Path | None,
    runtime_store: Any | None,
    tool_context: Any,
    operation: str = "read",
    direct: bool = False,
) -> tuple[Any, str] | None:
    """Return the DB-backed memory store for paths under ``<workspace>/memory``.

    Memory paths are durable cluster state and never fall back to the host
    filesystem: when the run has no usable memory scope (memory not enabled or
    not configured) a ToolError is raised instead. Returns None only for paths
    outside the memory root, which follow normal scratch/workspace handling.
    """
    if workspace is None or not isinstance(tool_context, ToolExecutionContext):
        return None
    memory_root = (workspace / "memory").resolve()
    try:
        relative = file_path.resolve().relative_to(memory_root)
    except ValueError:
        return None
    relative_path = relative.as_posix()
    if not relative_path:
        return None
    if runtime_store is None or not tool_context.memory_scope:
        raise ToolError(
            "memory_unavailable",
            f"memory {operation} is unavailable for {relative_path}: "
            "memory is not enabled or configured for this run",
        )
    from joyhousebot.agent.memory import MemoryStore

    policy = EffectiveMemoryPolicy.from_dict(tool_context.memory_policy)
    if not policy.allows_path(relative_path, operation, direct=direct):
        raise ToolError(
            "memory_policy",
            f"memory {operation} is disabled for {relative_path} by the Agent policy",
        )
    return (
        MemoryStore(runtime_store, scope_key=tool_context.memory_scope),
        relative_path,
    )


class ReadFileTool(Tool):
    """Tool to read file contents."""

    def __init__(
        self,
        allowed_dir: Path | None = None,
        *,
        workspace: Path | None = None,
        runtime_store: Any | None = None,
    ):
        _warn_if_unrestricted(type(self).__name__, allowed_dir)
        self._allowed_dir = allowed_dir
        self._workspace = workspace
        self._runtime_store = runtime_store

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
            "properties": {"path": {"type": "string", "description": "The file path to read"}},
            "required": ["path"],
        }

    @tool_error_handler("Failed to read file")
    async def execute(self, path: str, **kwargs: Any) -> str:
        file_path = _resolve_path(
            path,
            self._allowed_dir,
            workspace=self._workspace,
            tool_context=kwargs.get("tool_context"),
        )
        scoped = _scoped_memory_store(
            file_path,
            workspace=self._workspace,
            runtime_store=self._runtime_store,
            tool_context=kwargs.get("tool_context"),
        )
        if scoped is not None:
            content = scoped[0].read_relative(scoped[1])
            if not content:
                raise ToolError(self.name, f"File not found: {path}")
            return content
        if not file_path.exists():
            raise ToolError(self.name, f"File not found: {path}")
        if not file_path.is_file():
            raise ToolError(self.name, f"Not a file: {path}")
        _validate_file_size(file_path)
        return file_path.read_text(encoding="utf-8")


class WriteFileTool(Tool):
    """Tool to write content to a file."""

    def __init__(
        self,
        allowed_dir: Path | None = None,
        *,
        workspace: Path | None = None,
        runtime_store: Any | None = None,
    ):
        _warn_if_unrestricted(type(self).__name__, allowed_dir)
        self._allowed_dir = allowed_dir
        self._workspace = workspace
        self._runtime_store = runtime_store

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
                "path": {"type": "string", "description": "The file path to write to"},
                "content": {"type": "string", "description": "The content to write"},
            },
            "required": ["path", "content"],
        }

    @tool_error_handler("Failed to write file")
    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        file_path = _resolve_path(
            path,
            self._allowed_dir,
            workspace=self._workspace,
            tool_context=kwargs.get("tool_context"),
        )
        scoped = _scoped_memory_store(
            file_path,
            workspace=self._workspace,
            runtime_store=self._runtime_store,
            tool_context=kwargs.get("tool_context"),
            operation="write",
            direct=True,
        )
        if scoped is not None:
            scoped[0].write_relative(scoped[1], content)
            return f"Successfully wrote {len(content)} bytes to scoped memory {scoped[1]}"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(file_path, content)
        return f"Successfully wrote {len(content)} bytes to {path}"


class EditFileTool(Tool):
    """Tool to edit a file by replacing text."""

    def __init__(
        self,
        allowed_dir: Path | None = None,
        *,
        workspace: Path | None = None,
        runtime_store: Any | None = None,
    ):
        _warn_if_unrestricted(type(self).__name__, allowed_dir)
        self._allowed_dir = allowed_dir
        self._workspace = workspace
        self._runtime_store = runtime_store

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
                "path": {"type": "string", "description": "The file path to edit"},
                "old_text": {"type": "string", "description": "The exact text to find and replace"},
                "new_text": {"type": "string", "description": "The text to replace with"},
            },
            "required": ["path", "old_text", "new_text"],
        }

    @tool_error_handler("Failed to edit file")
    async def execute(self, path: str, old_text: str, new_text: str, **kwargs: Any) -> str:
        file_path = _resolve_path(
            path,
            self._allowed_dir,
            workspace=self._workspace,
            tool_context=kwargs.get("tool_context"),
        )
        scoped = _scoped_memory_store(
            file_path,
            workspace=self._workspace,
            runtime_store=self._runtime_store,
            tool_context=kwargs.get("tool_context"),
            operation="write",
            direct=True,
        )
        if scoped is not None:
            context = kwargs.get("tool_context")
            policy = EffectiveMemoryPolicy.from_dict(context.memory_policy)
            if not policy.allows_path(scoped[1], "read"):
                raise ToolError(self.name, f"memory read is disabled for {scoped[1]}")
            content = scoped[0].read_relative(scoped[1])
            if not content:
                raise ToolError(self.name, f"File not found: {path}")
        else:
            if not file_path.exists():
                raise ToolError(self.name, f"File not found: {path}")
            content = file_path.read_text(encoding="utf-8")

        if old_text not in content:
            raise ToolError(self.name, "old_text not found in file. Make sure it matches exactly.")

        count = content.count(old_text)
        if count > 1:
            raise ToolError(
                self.name,
                f"old_text appears {count} times. Please provide more context to make it unique.",
                is_recoverable=True,
            )

        new_content = content.replace(old_text, new_text, 1)
        if scoped is not None:
            scoped[0].write_relative(scoped[1], new_content)
        else:
            _atomic_write_text(file_path, new_content)

        return f"Successfully edited {path}"


class ListDirTool(Tool):
    """Tool to list directory contents."""

    def __init__(
        self,
        allowed_dir: Path | None = None,
        *,
        workspace: Path | None = None,
        runtime_store: Any | None = None,
    ):
        _warn_if_unrestricted(type(self).__name__, allowed_dir)
        self._allowed_dir = allowed_dir
        self._workspace = workspace
        self._runtime_store = runtime_store

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
            "properties": {"path": {"type": "string", "description": "The directory path to list"}},
            "required": ["path"],
        }

    @tool_error_handler("Failed to list directory")
    async def execute(self, path: str, **kwargs: Any) -> str:
        tool_context = kwargs.get("tool_context")
        dir_path = _resolve_path(
            path,
            self._allowed_dir,
            workspace=self._workspace,
            tool_context=tool_context,
        )
        if self._workspace is not None and isinstance(tool_context, ToolExecutionContext):
            memory_root = (self._workspace / "memory").resolve()
            try:
                relative = dir_path.resolve().relative_to(memory_root)
            except ValueError:
                relative = None
            if relative is not None:
                # Memory paths are DB-backed cluster state; never list the
                # host filesystem as a fallback when memory is unavailable.
                if self._runtime_store is None or not tool_context.memory_scope:
                    raise ToolError(
                        self.name,
                        "memory read is unavailable: memory is not enabled or configured "
                        "for this run",
                    )
                from joyhousebot.agent.memory import MemoryStore

                policy = EffectiveMemoryPolicy.from_dict(tool_context.memory_policy)
                if not policy.layer_enabled("episodic", "read") and not policy.layer_enabled(
                    "profile", "read"
                ) and not policy.layer_enabled("long_term", "read"):
                    raise ToolError(self.name, "memory read is disabled by the Agent policy")
                store = MemoryStore(self._runtime_store, scope_key=tool_context.memory_scope)
                items = store.list_relative("/".join(relative.parts))
                if not items:
                    return f"Directory {path} is empty"
                return "\n".join(
                    f"{'[DIR] ' if is_directory else '[FILE] '}{name}"
                    for name, is_directory in items
                )
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
