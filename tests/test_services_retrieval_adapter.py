from pathlib import Path

import pytest

from joyhousebot.services.memory.store import MemoryStore
from joyhousebot.services.retrieval.adapter import search_async
from joyhousebot.services.retrieval.knowledge_repository import KnowledgeRepository
from tests.support.postgres_store import PostgresTestStore


@pytest.mark.asyncio
async def test_retrieval_requires_durable_store(tmp_path: Path) -> None:
    assert await search_async(query="anything") == []


@pytest.mark.asyncio
async def test_knowledge_retrieval_is_user_scoped(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "retrieval.db")
    KnowledgeRepository(store).index_document(
        doc_id="doc-a",
        user_id="user-a",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="Private",
        chunks=[{"text": "distributed gateway design"}],
    )
    hits = await search_async(
        query="gateway",
        scope="knowledge",
        runtime_store=store,
        user_id="user-a",
    )
    assert hits[0]["doc_id"] == "doc-a"
    assert (
        await search_async(
            query="gateway",
            scope="knowledge",
            runtime_store=store,
            user_id="user-b",
        )
        == []
    )


@pytest.mark.asyncio
async def test_knowledge_retrieval_can_scope_to_product_collection(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "retrieval-collections.db")
    repository = KnowledgeRepository(store)
    repository.index_document(
        doc_id="doc-market",
        user_id="user-a",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="Market",
        chunks=[{"text": "shared keyword opportunity"}],
        metadata={"collection_refs": ["collection-market"]},
    )
    repository.index_document(
        doc_id="doc-growth",
        user_id="user-a",
        agent_id="default",
        source_type="note",
        source_url=None,
        title="Growth",
        chunks=[{"text": "shared keyword reflection"}],
        metadata={"collection_refs": ["collection-growth"]},
    )
    hits = await search_async(
        query="shared keyword",
        scope="knowledge",
        collection_ref="collection-market",
        runtime_store=store,
        user_id="user-a",
    )
    assert [item["doc_id"] for item in hits] == ["doc-market"]


@pytest.mark.asyncio
async def test_memory_retrieval_reads_scoped_repository(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "memory.db")
    MemoryStore(store, "user:user-a:agent:default").write_long_term("User prefers concise answers.")
    hits = await search_async(
        query="concise",
        scope="memory",
        memory_scope_key="user:user-a:agent:default",
        runtime_store=store,
        user_id="user-a",
    )
    assert any("concise" in hit["content"] for hit in hits)


@pytest.mark.asyncio
async def test_memory_retrieval_without_scope_key_returns_nothing(tmp_path: Path) -> None:
    """A run without a resolved memory scope must not fall back to the
    cluster-wide "shared" scope."""
    store = PostgresTestStore(tmp_path / "memory.db")
    MemoryStore(store, "shared").write_long_term("cluster shared fact")
    hits = await search_async(
        query="shared",
        scope="memory",
        memory_scope_key=None,
        runtime_store=store,
        user_id="user-a",
    )
    assert hits == []
