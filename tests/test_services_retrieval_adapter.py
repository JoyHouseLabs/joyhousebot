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
