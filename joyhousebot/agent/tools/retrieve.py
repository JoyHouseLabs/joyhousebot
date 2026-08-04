"""Retrieve durable user-scoped knowledge and memory."""

import json
from typing import Any

from loguru import logger

from joyhousebot.agent.memory_policy import EffectiveMemoryPolicy
from joyhousebot.agent.tools.base import Tool
from joyhousebot.capabilities.tool_adapter import ToolInvocationError
from joyhousebot.runtime.context import ToolExecutionContext
from joyhousebot.utils.exceptions import (
    ToolError,
    ValidationError,
    classify_exception,
    sanitize_error_message,
)


class RetrieveTool(Tool):
    """Search durable scoped knowledge or memory records."""

    def __init__(
        self,
        runtime_store: Any,
    ):
        if runtime_store is None:
            raise ValueError("RetrieveTool requires a durable runtime_store")
        self.runtime_store = runtime_store

    @property
    def name(self) -> str:
        return "retrieve"

    @property
    def description(self) -> str:
        return (
            "Search durable user-scoped knowledge and memory documents. "
            "Returns matching text chunks with source trace (doc_id, source_url/file_path, page). "
            "Use for evidence-backed answers and decision support."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (full-text)"},
                "top_k": {
                    "type": "integer",
                    "description": "Max results (default 10)",
                    "minimum": 1,
                    "maximum": 50,
                },
                "source_type": {
                    "type": "string",
                    "enum": ["url", "note"],
                    "description": "Optional: filter by source type",
                },
                "scope": {
                    "type": "string",
                    "enum": ["knowledge", "memory"],
                    "description": "knowledge = indexed documents; memory = durable Agent memory",
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
            tool_context = kwargs.get("tool_context")
            if not isinstance(tool_context, ToolExecutionContext):
                raise ToolError(self.name, "durable run context is required")
            if scope == "memory" and not EffectiveMemoryPolicy.from_dict(
                tool_context.memory_policy
            ).can_read_tools:
                raise ToolInvocationError(
                    "MEMORY_ACCESS_DENIED",
                    "memory retrieval is disabled by this Agent memory policy",
                )
            from joyhousebot.services.retrieval.adapter import search_async

            hits = await search_async(
                query=query,
                top_k=top_k,
                source_type=source_type,
                scope=scope,
                memory_scope_key=tool_context.memory_scope,
                runtime_store=self.runtime_store,
                user_id=tool_context.user_id,
            )
        except ToolError:
            raise
        except FileNotFoundError as e:
            logger.warning(f"Knowledge base not found: {e}")
            raise ToolInvocationError("KNOWLEDGE_NOT_INITIALIZED", "Knowledge base not initialized") from e
        except Exception as e:
            code, category, _ = classify_exception(e)
            sanitized = sanitize_error_message(str(e))
            logger.error(f"Retrieve error [{code}]: {sanitized}")
            raise ToolInvocationError(code, sanitized, retryable=category == "transient") from e

        return json.dumps(
            {"query": query, "scope": scope, "count": len(hits), "hits": hits},
            ensure_ascii=False,
            indent=2,
        )
