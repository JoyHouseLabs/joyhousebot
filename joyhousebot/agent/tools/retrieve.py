"""Retrieve tool: pluggable search over knowledge base (FTS5+vector) and optional memory scope via adapter."""

import json
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.agent.tools.base import Tool
from joyhousebot.utils.exceptions import (
    ToolError,
    ValidationError,
    sanitize_error_message,
    classify_exception,
)


class RetrieveTool(Tool):
    """Search the knowledge base (ingested documents). Returns matching chunks with evidence trace.
    Uses retrieval adapter: builtin hybrid by default; memory scope can use configurable backend (e.g. MCP qmd).
    """

    def __init__(
        self,
        workspace: Path,
        config: Any = None,
        mcp_memory_search_callable: Any = None,
        mcp_knowledge_search_callable: Any = None,
    ):
        self.workspace = Path(workspace)
        self.config = config
        self._mcp_memory_search_callable = mcp_memory_search_callable
        self._mcp_knowledge_search_callable = mcp_knowledge_search_callable
        self._memory_scope_key: str | None = None

    def set_memory_scope(self, scope_key: str | None) -> None:
        """Set current memory scope for scope=memory searches (per-session/per-user isolation)."""
        self._memory_scope_key = scope_key

    def set_mcp_memory_search_callable(self, callable: Any) -> None:
        """Inject MCP memory search callable (e.g. after QMD connects)."""
        self._mcp_memory_search_callable = callable

    def set_mcp_knowledge_search_callable(self, callable: Any) -> None:
        """Inject MCP knowledge search callable (e.g. after QMD connects)."""
        self._mcp_knowledge_search_callable = callable

    @property
    def name(self) -> str:
        return "retrieve"

    @property
    def description(self) -> str:
        return (
            "Search the knowledge base (documents from workspace/knowledgebase pipeline: files there are converted to markdown and indexed). "
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
                    "description": "knowledge = docs from knowledgebase pipeline; memory = L0/L1/L2 memory (if backend configured)",
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
            raise ValidationError("query is required", field="query")

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
                mcp_knowledge_search_callable=self._mcp_knowledge_search_callable,
                memory_scope_key=self._memory_scope_key,
            )
        except ToolError:
            raise
        except FileNotFoundError as e:
            logger.warning(f"Knowledge base not found: {e}")
            return json.dumps({"error": "Knowledge base not initialized", "hits": []})
        except Exception as e:
            code, category, _ = classify_exception(e)
            sanitized = sanitize_error_message(str(e))
            logger.error(f"Retrieve error [{code}]: {sanitized}")
            return json.dumps({"error": sanitized, "code": code, "hits": []})

        return json.dumps(
            {"query": query, "scope": scope, "count": len(hits), "hits": hits},
            ensure_ascii=False,
            indent=2,
        )

