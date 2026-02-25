"""Pluggable retrieval adapter: builtin (FTS5+vector), optional MCP (e.g. qmd) for memory and knowledge."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Awaitable, Callable

from joyhousebot.services.retrieval.hybrid import hybrid_search_async
from joyhousebot.services.retrieval.memory_search import search_memory_files
from joyhousebot.services.retrieval.memory_vector_store import search_memory_sqlite_vector
from joyhousebot.services.retrieval.vector_optional import get_memory_embedding_provider


def _cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def search_async(
    workspace: Path,
    config: Any,
    query: str,
    top_k: int = 10,
    source_type: str | None = None,
    doc_id: str | None = None,
    scope: str = "knowledge",
    mcp_memory_search_callable: Callable[[str, int], Awaitable[list[dict[str, Any]]]] | None = None,
    mcp_knowledge_search_callable: Callable[[str, int], Awaitable[list[dict[str, Any]]]] | None = None,
    memory_scope_key: str | None = None,
) -> list[dict[str, Any]]:
    """
    Unified search: knowledge scope uses knowledge_backend (builtin = FTS5+Chroma, or qmd when callable provided);
    memory scope uses memory_backend (builtin = grep, or mcp_qmd / sqlite_vector when configured).
    """
    query = (query or "").strip()
    if not query:
        return []

    if scope == "knowledge":
        retrieval_cfg = getattr(getattr(config, "tools", None), "retrieval", None)
        knowledge_backend = (getattr(retrieval_cfg, "knowledge_backend", "builtin") or "builtin").strip().lower()
        if knowledge_backend in ("qmd", "auto") and mcp_knowledge_search_callable:
            try:
                hits = await mcp_knowledge_search_callable(query, top_k)
                if isinstance(hits, list):
                    return hits
            except Exception:
                pass
        return await hybrid_search_async(
            workspace, config, query=query, top_k=top_k, source_type=source_type, doc_id=doc_id
        )

    if scope == "memory":
        retrieval_cfg = getattr(getattr(config, "tools", None), "retrieval", None)
        memory_backend = (getattr(retrieval_cfg, "memory_backend", "builtin") or "builtin").strip().lower()
        mem_top_k = getattr(retrieval_cfg, "memory_top_k", top_k) if retrieval_cfg else top_k
        memory_vector = getattr(retrieval_cfg, "memory_vector_enabled", False) if retrieval_cfg else False

        # mcp_qmd: try first when backend is mcp_qmd or auto
        if memory_backend in ("mcp_qmd", "auto") and mcp_memory_search_callable:
            try:
                hits = await mcp_memory_search_callable(query, mem_top_k)
                if isinstance(hits, list):
                    return hits
            except Exception:
                pass

        # sqlite_vector: try when backend is sqlite_vector or (auto and mcp_qmd failed)
        if memory_backend in ("sqlite_vector", "auto"):
            try:
                hits = await search_memory_sqlite_vector(
                    workspace, config, query=query, top_k=mem_top_k, scope_key=memory_scope_key
                )
                if hits is not None:
                    return hits
            except Exception:
                pass

        # builtin: grep over memory files (default or fallback)
        fetch_k = max(mem_top_k * 4, 20) if memory_vector else mem_top_k
        hits = search_memory_files(workspace, query=query, top_k=fetch_k, scope_key=memory_scope_key)
        if memory_vector and hits:
            provider = get_memory_embedding_provider(config)
            if provider is not None:
                try:
                    texts = [h["content"] for h in hits]
                    query_vec, *hit_vecs = await provider.aembed([query] + texts)
                    if query_vec and len(hit_vecs) == len(hits):
                        scored = [(h, _cosine_sim(query_vec, v)) for h, v in zip(hits, hit_vecs)]
                        scored.sort(key=lambda x: -x[1])
                        hits = [h for h, _ in scored[:mem_top_k]]
                except Exception:
                    hits = hits[:mem_top_k]
        else:
            hits = hits[:mem_top_k]
        return hits

    return await hybrid_search_async(
        workspace, config, query=query, top_k=top_k, source_type=source_type, doc_id=doc_id
    )
