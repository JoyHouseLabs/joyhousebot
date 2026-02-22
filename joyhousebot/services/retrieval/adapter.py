"""Pluggable retrieval adapter: builtin (FTS5+vector) and optional MCP (e.g. qmd) for memory scope."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from joyhousebot.services.retrieval.hybrid import hybrid_search_async


async def search_async(
    workspace: Path,
    config: Any,
    query: str,
    top_k: int = 10,
    source_type: str | None = None,
    doc_id: str | None = None,
    scope: str = "knowledge",
    mcp_memory_search_callable: Callable[[str, int], Awaitable[list[dict[str, Any]]]] | None = None,
) -> list[dict[str, Any]]:
    """
    Unified search: knowledge scope always uses builtin (hybrid); memory scope uses
    memory_backend (builtin or mcp_qmd when callable provided), with fallback to builtin.
    """
    query = (query or "").strip()
    if not query:
        return []

    if scope == "memory":
        retrieval_cfg = getattr(getattr(config, "tools", None), "retrieval", None)
        memory_backend = getattr(retrieval_cfg, "memory_backend", "builtin") if retrieval_cfg else "builtin"
        mem_top_k = getattr(retrieval_cfg, "memory_top_k", top_k) if retrieval_cfg else top_k
        try:
            if memory_backend in ("mcp_qmd", "auto") and mcp_memory_search_callable:
                hits = await mcp_memory_search_callable(query, mem_top_k)
                if isinstance(hits, list):
                    return hits
        except Exception:
            pass
        # Fallback: builtin (knowledge hybrid; memory index can be added later)
        return await hybrid_search_async(
            workspace, config, query=query, top_k=mem_top_k, source_type=source_type, doc_id=doc_id
        )

    return await hybrid_search_async(
        workspace, config, query=query, top_k=top_k, source_type=source_type, doc_id=doc_id
    )
