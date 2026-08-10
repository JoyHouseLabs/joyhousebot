"""Durable user-scoped retrieval over normalized knowledge and memory tables."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from joyhousebot.services.memory.store import MemoryStore
from joyhousebot.services.retrieval.knowledge_repository import KnowledgeRepository


async def search_async(
    query: str,
    *,
    top_k: int = 10,
    source_type: str | None = None,
    doc_id: str | None = None,
    scope: str = "knowledge",
    memory_scope_key: str | None = None,
    runtime_store: Any = None,
    user_id: str = "system",
) -> list[dict[str, Any]]:
    """Search durable state; local files and process-local vector stores are not fallbacks."""
    query = query.strip()
    if not query or runtime_store is None:
        return []
    limit = max(1, min(int(top_k), 50))
    if scope == "knowledge":
        repository = getattr(runtime_store, "_knowledge_repository", None)
        if repository is None:
            repository = KnowledgeRepository(runtime_store)
            runtime_store._knowledge_repository = repository
        return repository.search(
            user_id=user_id,
            query=query,
            top_k=limit,
            source_type=source_type,
            doc_id=doc_id,
        )
    if scope != "memory":
        return []
    if not memory_scope_key:
        # No resolved run scope means memory is not configured for this run;
        # never fall back to the cluster-wide "shared" scope implicitly.
        return []
    store = MemoryStore(runtime_store, scope_key=memory_scope_key)
    documents = store.repository.list_documents(store.scope_key) if store.repository else {}
    lowered = query.casefold()
    hits: list[dict[str, Any]] = []
    for path, content in documents.items():
        for index, line in enumerate(content.splitlines()):
            if lowered not in line.casefold():
                continue
            hits.append(
                {
                    "doc_id": path,
                    "source_type": "memory",
                    "source_url": "",
                    "file_path": path,
                    "title": Path(path).name,
                    "chunk_index": index,
                    "page": None,
                    "content": line[:700],
                    "trace": {"doc_id": path, "source": path, "page": None},
                }
            )
            if len(hits) >= limit:
                return hits
    return hits
