"""Retrieve tool: pluggable search over knowledge base (FTS5+vector) and optional memory scope via adapter."""

import json
from pathlib import Path
from typing import Any

from joyhousebot.agent.tools.base import Tool


class RetrieveTool(Tool):
    """Search the knowledge base (ingested documents). Returns matching chunks with evidence trace.
    Uses retrieval adapter: builtin hybrid by default; memory scope can use configurable backend (e.g. MCP qmd).
    """

    def __init__(self, workspace: Path, config: Any = None, mcp_memory_search_callable: Any = None):
        self.workspace = Path(workspace)
        self.config = config
        self._mcp_memory_search_callable = mcp_memory_search_callable

    @property
    def name(self) -> str:
        return "retrieve"

    @property
    def description(self) -> str:
        return (
            "Search the knowledge base (documents previously imported with ingest). "
            "Returns matching text chunks with source trace (doc_id, source_url/file_path, page). "
            "Use for evidence-backed answers and decision support."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (full-text)"},
                "top_k": {"type": "integer", "description": "Max results (default 10)", "minimum": 1, "maximum": 50},
                "source_type": {
                    "type": "string",
                    "enum": ["pdf", "url", "image", "youtube"],
                    "description": "Optional: filter by source type",
                },
                "scope": {
                    "type": "string",
                    "enum": ["knowledge", "memory"],
                    "description": "knowledge = ingested docs; memory = L0/L1/L2 memory (if backend configured)",
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        query: str,
        top_k: int = 10,
        source_type: str | None = None,
        scope: str = "knowledge",
        **kwargs: Any,
    ) -> str:
        query = (query or "").strip()
        if not query:
            return json.dumps({"error": "query is required", "hits": []})

        try:
            from joyhousebot.services.retrieval.adapter import search_async
            hits = await search_async(
                self.workspace,
                self.config,
                query=query,
                top_k=top_k,
                source_type=source_type,
                scope=scope,
                mcp_memory_search_callable=self._mcp_memory_search_callable,
            )
        except Exception as e:
            return json.dumps({"error": str(e), "hits": []})

        return json.dumps(
            {"query": query, "scope": scope, "count": len(hits), "hits": hits},
            ensure_ascii=False,
            indent=2,
        )

