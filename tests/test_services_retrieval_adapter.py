"""Tests for retrieval adapter: builtin fallback and scope behavior."""

import pytest
from pathlib import Path

from joyhousebot.services.retrieval.adapter import search_async


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "knowledge").mkdir(exist_ok=True)
    return tmp_path


@pytest.fixture
def config_none() -> None:
    return None


@pytest.mark.asyncio
async def test_search_async_knowledge_scope_uses_builtin(workspace: Path) -> None:
    # No knowledge indexed -> empty hits, but no error
    hits = await search_async(workspace, None, query="test", top_k=5, scope="knowledge")
    assert isinstance(hits, list)
    # With no DB, builtin may return [] or raise; we expect list
    assert len(hits) == 0 or all("content" in h or "doc_id" in h for h in hits)


@pytest.mark.asyncio
async def test_search_async_memory_scope_fallback_without_callable(workspace: Path) -> None:
    # memory scope without mcp callable -> fallback to builtin (same as knowledge when no memory index)
    hits = await search_async(
        workspace, None, query="memory query", top_k=5, scope="memory"
    )
    assert isinstance(hits, list)


@pytest.mark.asyncio
async def test_search_async_empty_query_returns_empty(workspace: Path) -> None:
    hits = await search_async(workspace, None, query="", top_k=5)
    assert hits == []
    hits = await search_async(workspace, None, query="   ", top_k=5)
    assert hits == []


@pytest.mark.asyncio
async def test_search_async_memory_scope_returns_hits_when_memory_has_content(workspace: Path) -> None:
    memory_dir = workspace / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text("User prefers dark mode and short answers.")
    hits = await search_async(
        workspace, None, query="dark mode", top_k=5, scope="memory"
    )
    assert isinstance(hits, list)
    assert len(hits) >= 1
    assert any("dark mode" in h.get("content", "") for h in hits)


@pytest.mark.asyncio
async def test_search_async_memory_scope_key_searches_scoped_dir(workspace: Path) -> None:
    memory_dir = workspace / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text("Shared content.")
    scope_dir = memory_dir / "user_42"
    scope_dir.mkdir()
    (scope_dir / "MEMORY.md").write_text("User 42 private note: vacation plans.")
    hits_shared = await search_async(
        workspace, None, query="vacation plans", top_k=5, scope="memory", memory_scope_key=None
    )
    assert len(hits_shared) == 0
    hits_scoped = await search_async(
        workspace, None, query="vacation plans", top_k=5, scope="memory", memory_scope_key="user_42"
    )
    assert len(hits_scoped) >= 1
    assert any("vacation" in h.get("content", "") for h in hits_scoped)


@pytest.mark.asyncio
async def test_search_async_memory_sqlite_vector_fallback_to_builtin(workspace: Path) -> None:
    """When memory_backend=sqlite_vector but embedding not configured, fallback to builtin grep."""
    from types import SimpleNamespace
    memory_dir = workspace / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text("Semantic fallback test: keyword here.")
    config = SimpleNamespace(
        tools=SimpleNamespace(
            retrieval=SimpleNamespace(
                memory_backend="sqlite_vector",
                memory_top_k=5,
                memory_vector_enabled=False,
                embedding_model="",  # not configured -> sqlite_vector returns None -> fallback builtin
            )
        )
    )
    hits = await search_async(workspace, config, query="keyword here", top_k=5, scope="memory")
    assert isinstance(hits, list)
    assert len(hits) >= 1
    assert any("keyword here" in h.get("content", "") for h in hits)
