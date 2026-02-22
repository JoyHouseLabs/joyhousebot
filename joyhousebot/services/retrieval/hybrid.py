"""Hybrid search: FTS5 + vector recall with RRF fusion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from joyhousebot.services.retrieval.store import RetrievalStore
from joyhousebot.services.retrieval.vector_optional import (
    get_embedding_provider,
    get_vector_store,
    should_enable_vector,
)

RRF_K = 60


def _hit_key(h: dict[str, Any]) -> str:
    return f"{h.get('doc_id', '')}:{h.get('chunk_index', 0)}"


def _vector_meta_to_hit(meta: dict[str, Any]) -> dict[str, Any]:
    """Build hit dict from Chroma metadata (same shape as FTS5 hit)."""
    doc_id = meta.get("doc_id", "")
    source_url = meta.get("source_url", "")
    file_path = meta.get("file_path", "")
    page = meta.get("page")
    return {
        "doc_id": doc_id,
        "source_type": meta.get("source_type", ""),
        "source_url": source_url,
        "file_path": file_path,
        "title": meta.get("title", ""),
        "chunk_index": int(meta.get("chunk_index", 0)),
        "page": page if page is not None else None,
        "content": meta.get("content", ""),
        "trace": {
            "doc_id": doc_id,
            "source": source_url or file_path,
            "page": page,
        },
    }


def hybrid_search(
    workspace: Path,
    config: Any,
    query: str,
    top_k: int = 10,
    source_type: str | None = None,
    doc_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Search knowledge base: FTS5 only, or FTS5 + vector with RRF fusion when vector is enabled.
    Returns list of hits in same format as RetrievalStore.search.
    """
    query = (query or "").strip()
    if not query:
        return []

    store = RetrievalStore(workspace)
    fts_hits = store.search(query=query, top_k=top_k, source_type=source_type, doc_id=doc_id)

    if not should_enable_vector(workspace, config):
        return fts_hits

    embedding_provider = get_embedding_provider(config)
    vector_store = get_vector_store(workspace, config)
    if not embedding_provider or not vector_store:
        return fts_hits

    # Vector recall
    try:
        vectors = embedding_provider.embed([query])
        if not vectors:
            return fts_hits
        query_vector = vectors[0]
    except Exception:
        return fts_hits

    vec_results = vector_store.search(
        query_vector=query_vector,
        top_k=top_k,
        source_type=source_type,
    )

    if not vec_results:
        return fts_hits

    # Key -> hit (prefer FTS5 hit for consistent structure)
    key_to_hit: dict[str, dict[str, Any]] = {}
    for h in fts_hits:
        key_to_hit[_hit_key(h)] = h
    for cid, _score, meta in vec_results:
        key = cid
        if key not in key_to_hit:
            key_to_hit[key] = _vector_meta_to_hit(meta)

    # RRF ranks (1-based)
    rank_fts: dict[str, int] = {_hit_key(h): r for r, h in enumerate(fts_hits, 1)}
    rank_vec: dict[str, int] = {cid: r for r, (cid, _, _) in enumerate(vec_results, 1)}

    all_keys = set(rank_fts) | set(rank_vec)
    rrf_scores = [
        (key, 1.0 / (RRF_K + rank_fts.get(key, 999)) + 1.0 / (RRF_K + rank_vec.get(key, 999)))
        for key in all_keys
    ]
    rrf_scores.sort(key=lambda x: -x[1])

    top_keys = [k for k, _ in rrf_scores[:top_k]]
    return [key_to_hit[k] for k in top_keys if k in key_to_hit]


async def hybrid_search_async(
    workspace: Path,
    config: Any,
    query: str,
    top_k: int = 10,
    source_type: str | None = None,
    doc_id: str | None = None,
) -> list[dict[str, Any]]:
    """Async variant: uses aembed for query vector when vector is enabled."""
    query = (query or "").strip()
    if not query:
        return []

    store = RetrievalStore(workspace)
    fts_hits = store.search(query=query, top_k=top_k, source_type=source_type, doc_id=doc_id)

    if not should_enable_vector(workspace, config):
        return fts_hits

    embedding_provider = get_embedding_provider(config)
    vector_store = get_vector_store(workspace, config)
    if not embedding_provider or not vector_store:
        return fts_hits

    try:
        vectors = await embedding_provider.aembed([query])
        if not vectors:
            return fts_hits
        query_vector = vectors[0]
    except Exception:
        return fts_hits

    vec_results = vector_store.search(
        query_vector=query_vector,
        top_k=top_k,
        source_type=source_type,
    )

    if not vec_results:
        return fts_hits

    key_to_hit = {_hit_key(h): h for h in fts_hits}
    for cid, _score, meta in vec_results:
        if cid not in key_to_hit:
            key_to_hit[cid] = _vector_meta_to_hit(meta)

    rank_fts = {_hit_key(h): r for r, h in enumerate(fts_hits, 1)}
    rank_vec = {cid: r for r, (cid, _, _) in enumerate(vec_results, 1)}
    all_keys = set(rank_fts) | set(rank_vec)
    rrf_scores = [
        (key, 1.0 / (RRF_K + rank_fts.get(key, 999)) + 1.0 / (RRF_K + rank_vec.get(key, 999)))
        for key in all_keys
    ]
    rrf_scores.sort(key=lambda x: -x[1])
    top_keys = [k for k, _ in rrf_scores[:top_k]]
    return [key_to_hit[k] for k in top_keys if k in key_to_hit]
