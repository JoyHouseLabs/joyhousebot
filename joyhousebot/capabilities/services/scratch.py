"""Run-scoped scratch filesystem port."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path

from joyhousebot.contracts.capabilities import CapabilityContext

MAX_FILE_SIZE = 10 * 1024 * 1024


class ScratchPort:
    def __init__(self, scratch_root: Path | None) -> None:
        self._root = Path(scratch_root).expanduser().resolve() if scratch_root else None

    def resolve(self, context: CapabilityContext, path: str) -> Path:
        if self._root is None:
            raise RuntimeError("Run scratch service is unavailable")
        requested = Path(str(path or "").strip())
        if not str(requested) or requested.is_absolute():
            raise ValueError("scratch path must be relative")
        workflow_id = context.root_run_id or context.run_id
        scope = hashlib.sha256(
            f"{context.user_id}\0{context.agent_id}\0{workflow_id}".encode()
        ).hexdigest()[:24]
        root = (self._root / ".scratch" / scope).resolve()
        resolved = (root / requested).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise PermissionError("scratch path is outside the current Run scope") from exc
        root.mkdir(parents=True, exist_ok=True)
        return resolved

    async def read(self, context: CapabilityContext, *, path: str) -> str:
        file_path = self.resolve(context, path)
        if not file_path.exists():
            raise FileNotFoundError(path)
        if not file_path.is_file():
            raise IsADirectoryError(path)
        if file_path.stat().st_size > MAX_FILE_SIZE:
            raise ValueError(f"file exceeds the {MAX_FILE_SIZE}-byte read limit")
        return await asyncio.to_thread(file_path.read_text, encoding="utf-8")

    async def write(self, context: CapabilityContext, *, path: str, content: str) -> None:
        if len(content.encode("utf-8")) > MAX_FILE_SIZE:
            raise ValueError(f"file exceeds the {MAX_FILE_SIZE}-byte write limit")
        file_path = self.resolve(context, path)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        def replace() -> None:
            descriptor, temporary = tempfile.mkstemp(
                dir=str(file_path.parent),
                prefix=f"{file_path.name}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(content)
                os.replace(temporary, file_path)
            except BaseException:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise

        await asyncio.to_thread(replace)

    async def list(self, context: CapabilityContext, *, path: str) -> list[dict[str, object]]:
        directory = self.resolve(context, path)
        if not directory.exists():
            raise FileNotFoundError(path)
        if not directory.is_dir():
            raise NotADirectoryError(path)
        return [
            {"name": item.name, "is_directory": item.is_dir()}
            for item in sorted(directory.iterdir())
        ]


__all__ = ["MAX_FILE_SIZE", "ScratchPort"]
