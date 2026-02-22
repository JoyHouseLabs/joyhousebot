"""Tests for L0/L1/L2 memory structure and MemoryStore helpers."""

import pytest
from pathlib import Path

from joyhousebot.agent.memory import (
    MemoryStore,
    L0_ABSTRACT_FILENAME,
    INSIGHTS_DIR,
    LESSONS_DIR,
    ARCHIVE_DIR,
)


def test_ensure_memory_structure_creates_dirs_and_abstract(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_memory_structure()
    assert (store.memory_dir / INSIGHTS_DIR).is_dir()
    assert (store.memory_dir / LESSONS_DIR).is_dir()
    assert (store.memory_dir / ARCHIVE_DIR).is_dir()
    l0 = store.memory_dir / L0_ABSTRACT_FILENAME
    assert l0.is_file()
    content = l0.read_text()
    assert "memory index" in content
    assert "active topics" in content
    assert "retrieval hints" in content


def test_read_l0_abstract_empty_until_updated(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_memory_structure()
    raw = store.read_l0_abstract()
    assert "memory index" in raw
    store.update_l0_abstract("# custom index\n\n## topics\n- foo")
    assert "custom index" in store.read_l0_abstract()
    assert "foo" in store.read_l0_abstract()


def test_append_l2_daily_and_get_l2_path(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_memory_structure()
    path = store.get_l2_path("2026-02-22")
    assert path == store.memory_dir / "2026-02-22.md"
    store.append_l2_daily("2026-02-22", "Event: deployed v2.")
    store.append_l2_daily("2026-02-22", "Event: user asked about X.")
    text = path.read_text()
    assert "deployed v2" in text
    assert "user asked about X" in text


def test_get_memory_context_with_l0_truncates_long_l0(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.ensure_memory_structure()
    store.update_l0_abstract("x" * 2000)
    store.write_long_term("- [P0] User prefers short answers.")
    ctx = store.get_memory_context_with_l0(max_l0_chars=500)
    assert "(truncated)" in ctx
    assert "User prefers short answers" in ctx
