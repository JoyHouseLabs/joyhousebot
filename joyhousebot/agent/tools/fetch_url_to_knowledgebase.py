"""Tool: fetch a URL and save its content into workspace/knowledgebase for pipeline processing."""

import hashlib
import json
from typing import Any

from joyhousebot.agent.tools.base import Tool
from joyhousebot.agent.tools.ingest.url_ingest import fetch_and_ingest_url
from joyhousebot.capabilities.tool_adapter import ToolInvocationError
from joyhousebot.runtime.context import ToolExecutionContext
from joyhousebot.services.retrieval.knowledge_repository import KnowledgeRepository
from joyhousebot.utils.exceptions import sanitize_error_message


class FetchUrlToKnowledgebaseTool(Tool):
    """Fetch a URL and save its extracted content into workspace/knowledgebase. The knowledge pipeline will then convert and index it. Use this to add web pages to the knowledge base."""

    def __init__(self, runtime_store: Any):
        self.runtime_store = runtime_store
        self.repository = None
        if runtime_store is not None:
            self.repository = getattr(runtime_store, "_knowledge_repository", None)
            if self.repository is None:
                self.repository = KnowledgeRepository(runtime_store)
                runtime_store._knowledge_repository = self.repository

    @property
    def name(self) -> str:
        return "fetch_url_to_knowledgebase"

    @property
    def description(self) -> str:
        return (
            "Fetch a URL and index its readable content in durable user-scoped knowledge. "
            "Use for adding web pages or articles to the knowledge base."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "HTTP(S) URL to fetch"},
            },
            "required": ["url"],
        }

    async def execute(self, url: str, **kwargs: Any) -> str:
        url = (url or "").strip()
        if not url:
            raise ToolInvocationError("INVALID_PARAMETERS", "url is required")
        # Validate the runtime context before any network fetch happens.
        tool_context = kwargs.get("tool_context")
        if not isinstance(tool_context, ToolExecutionContext) or self.repository is None:
            raise ToolInvocationError(
                "CONTEXT_REQUIRED", "durable runtime context is required for knowledge ingestion"
            )
        try:
            doc = await fetch_and_ingest_url(url)
        except ValueError as e:
            raise ToolInvocationError("INVALID_URL", sanitize_error_message(str(e))) from e
        except Exception as e:
            raise ToolInvocationError(
                "FETCH_FAILED", sanitize_error_message(str(e)), retryable=True
            ) from e
        doc_id = hashlib.sha256(f"{tool_context.user_id}:{doc.source_url}".encode()).hexdigest()[
            :24
        ]
        self.repository.index_document(
            doc_id=doc_id,
            user_id=tool_context.user_id,
            agent_id=tool_context.agent_id,
            source_type=doc.source_type,
            source_url=doc.source_url,
            title=doc.title,
            chunks=[{"text": chunk.text, "page": chunk.page} for chunk in doc.chunks],
            metadata={"trace": doc.trace},
        )
        return json.dumps(
            {
                "ok": True,
                "doc_id": doc_id,
                "url": doc.source_url,
                "title": doc.title,
                "chunk_count": len(doc.chunks),
                "message": "Content indexed in the durable knowledge store.",
            },
            ensure_ascii=False,
            indent=2,
        )
