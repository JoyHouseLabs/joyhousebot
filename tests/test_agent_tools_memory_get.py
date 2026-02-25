"""Tests for memory_get tool: read memory files with optional scope and line range."""

import json
import pytest
from pathlib import Path

from joyhousebot.agent.tools.memory_get import MemoryGetTool


@pytest.fixture
def workspace_with_memory(tmp_path: Path) -> Path:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text("- [P0] User prefers Python.\n- [P1] Project X.")
    (memory_dir / "HISTORY.md").write_text("[2026-02-25 10:00] First entry.\n\n[2026-02-25 11:00] Second.")
    (memory_dir / "2026-02-25.md").write_text("Daily log line 1.\nDaily log line 2.\nDaily log line 3.")
    return tmp_path


@pytest.mark.asyncio
async def test_memory_get_reads_memory_md(workspace_with_memory: Path) -> None:
    tool = MemoryGetTool(workspace_with_memory)
    out = await tool.execute(path="memory/MEMORY.md")
    data = json.loads(out)
    assert data.get("text", "")
    assert "User prefers Python" in data["text"]
    assert data.get("path") == "memory/MEMORY.md"


@pytest.mark.asyncio
async def test_memory_get_missing_file_returns_empty_text(workspace_with_memory: Path) -> None:
    tool = MemoryGetTool(workspace_with_memory)
    out = await tool.execute(path="memory/nonexistent.md")
    data = json.loads(out)
    assert data.get("text") == ""
    assert data.get("path") == "memory/nonexistent.md"


@pytest.mark.asyncio
async def test_memory_get_path_traversal_returns_error(workspace_with_memory: Path) -> None:
    tool = MemoryGetTool(workspace_with_memory)
    out = await tool.execute(path="memory/../other/file.md")
    data = json.loads(out)
    assert data.get("text") == ""
    assert "error" in data or "path must be under memory" in data.get("error", "")


@pytest.mark.asyncio
async def test_memory_get_line_range(workspace_with_memory: Path) -> None:
    tool = MemoryGetTool(workspace_with_memory)
    out = await tool.execute(path="memory/2026-02-25.md", start_line=2, num_lines=2)
    data = json.loads(out)
    assert "Daily log line 2" in data["text"]
    assert "Daily log line 3" in data["text"]
    assert "Daily log line 1" not in data["text"]


@pytest.mark.asyncio
async def test_memory_get_with_scope_reads_scoped_file(workspace_with_memory: Path) -> None:
    scope_dir = workspace_with_memory / "memory" / "session_1"
    scope_dir.mkdir(parents=True)
    (scope_dir / "MEMORY.md").write_text("Session 1 private: secret plan.")
    tool = MemoryGetTool(workspace_with_memory)
    tool.set_memory_scope("session_1")
    out = await tool.execute(path="memory/session_1/MEMORY.md")
    data = json.loads(out)
    assert "secret plan" in data.get("text", "")
