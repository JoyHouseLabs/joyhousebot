"""Tests for ContextBuilder memory context (scope_key, L0, daily)."""

from pathlib import Path

import pytest

from joyhousebot.agent.context import ContextBuilder
from joyhousebot.agent.memory import MemoryStore


@pytest.fixture
def workspace_with_scoped_memory(tmp_path: Path) -> Path:
    store_shared = MemoryStore(tmp_path)
    store_shared.ensure_memory_structure()
    store_shared.write_long_term("- [P0] Shared memory fact.")
    scope_dir = tmp_path / "memory" / "session_1"
    scope_dir.mkdir(parents=True)
    (scope_dir / "MEMORY.md").write_text("- [P0] Session 1 only fact.")
    (scope_dir / "HISTORY.md").write_text("")
    return tmp_path


def test_memory_store_scope_key_isolates_content(workspace_with_scoped_memory: Path) -> None:
    store_shared = MemoryStore(workspace_with_scoped_memory, scope_key=None)
    store_scoped = MemoryStore(workspace_with_scoped_memory, scope_key="session_1")
    assert "Shared memory fact" in store_shared.get_memory_context()
    assert "Session 1 only fact" in store_scoped.get_memory_context()
    assert "Session 1 only fact" not in store_shared.get_memory_context()


def test_build_system_prompt_includes_memory_block(workspace_with_scoped_memory: Path) -> None:
    builder = ContextBuilder(workspace_with_scoped_memory)
    prompt = builder.build_system_prompt(skill_names=None, scope_key=None)
    assert "Memory" in prompt or "memory" in prompt
    assert "Shared memory fact" in prompt


def test_build_system_prompt_with_scope_key_includes_scoped_memory(workspace_with_scoped_memory: Path) -> None:
    builder = ContextBuilder(workspace_with_scoped_memory)
    prompt = builder.build_system_prompt(skill_names=None, scope_key="session_1")
    assert "Session 1 only fact" in prompt
