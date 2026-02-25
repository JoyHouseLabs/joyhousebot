"""Tests for builtin memory search (grep over memory files)."""

import pytest
from pathlib import Path

from joyhousebot.services.retrieval.memory_search import search_memory_files


def test_search_memory_files_empty_workspace(tmp_path: Path) -> None:
    hits = search_memory_files(tmp_path, query="foo", top_k=5)
    assert hits == []


def test_search_memory_files_no_memory_dir(tmp_path: Path) -> None:
    (tmp_path / "other").mkdir()
    hits = search_memory_files(tmp_path, query="foo", top_k=5)
    assert hits == []


def test_search_memory_files_finds_in_memory_md(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text("User prefers dark mode and short answers.")
    hits = search_memory_files(tmp_path, query="dark mode", top_k=5)
    assert len(hits) >= 1
    assert any("dark mode" in h.get("content", "") for h in hits)
    assert any(h.get("file_path") == "memory/MEMORY.md" for h in hits)


def test_search_memory_files_finds_in_history_md(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "HISTORY.md").write_text("[2026-02-25 10:00] Discussed API design.\n\n[2026-02-25 11:00] Deployed v2.")
    hits = search_memory_files(tmp_path, query="API design", top_k=5)
    assert len(hits) >= 1
    assert any("API design" in h.get("content", "") for h in hits)


def test_search_memory_files_empty_query_returns_empty(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text("Some content.")
    assert search_memory_files(tmp_path, query="", top_k=5) == []
    assert search_memory_files(tmp_path, query="   ", top_k=5) == []


def test_search_memory_files_finds_in_abstract(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / ".abstract").write_text("# memory index\n\n## active topics\n- API design\n\n## retrieval hints\n- REST")
    hits = search_memory_files(tmp_path, query="REST", top_k=5)
    assert len(hits) >= 1
    assert any("REST" in h.get("content", "") for h in hits)


def test_search_memory_files_finds_in_insights_and_lessons(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "insights").mkdir()
    (memory_dir / "lessons").mkdir()
    (memory_dir / "insights" / "insights-2026-02-25.md").write_text("# Insights\n\n- We chose REST for the API.")
    (memory_dir / "lessons" / "lessons-2026-02-25.md").write_text("# Lessons\n\n- Always add tests.")
    hits = search_memory_files(tmp_path, query="REST", top_k=5)
    assert len(hits) >= 1
    assert any("REST" in h.get("content", "") for h in hits)
    hits2 = search_memory_files(tmp_path, query="add tests", top_k=5)
    assert len(hits2) >= 1


def test_search_memory_files_respects_top_k(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    lines = "\n".join(f"Line with keyword here number {i}" for i in range(20))
    (memory_dir / "MEMORY.md").write_text(lines)
    hits = search_memory_files(tmp_path, query="keyword", top_k=3)
    assert len(hits) <= 3


def test_search_memory_files_with_scope_key(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text("Shared memory.")
    scope_dir = memory_dir / "session_abc"
    scope_dir.mkdir()
    (scope_dir / "MEMORY.md").write_text("Session-specific secret keyword.")
    hits_shared = search_memory_files(tmp_path, query="secret keyword", top_k=5, scope_key=None)
    assert len(hits_shared) == 0
    hits_scoped = search_memory_files(tmp_path, query="secret keyword", top_k=5, scope_key="session_abc")
    assert len(hits_scoped) >= 1
    assert any("secret keyword" in h.get("content", "") for h in hits_scoped)
