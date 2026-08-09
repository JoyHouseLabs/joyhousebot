"""Owner-scoped use cases for durable Knowledge assets."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from joyhousebot.application.context import RequestContext
from joyhousebot.application.errors import ConflictError, NotFoundError, ValidationError
from joyhousebot.services.retrieval.knowledge_repository import KnowledgeRepository


class KnowledgeAssetService:
    """Expose the Runtime knowledge index through an owner control plane."""

    def __init__(self, store: Any) -> None:
        self.repository = KnowledgeRepository(store)

    async def list(
        self,
        context: RequestContext,
        *,
        knowledge_base_id: str | None = None,
        source_type: str | None = None,
        search: str | None = None,
        limit: int = 200,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if knowledge_base_id:
            knowledge_base = await asyncio.to_thread(
                self.repository.get_knowledge_base,
                user_id=context.user_id,
                knowledge_base_id=knowledge_base_id,
            )
            if knowledge_base is None:
                raise NotFoundError("Knowledge base not found")
        return await asyncio.gather(
            asyncio.to_thread(
                self.repository.list_documents,
                user_id=context.user_id,
                knowledge_base_id=knowledge_base_id,
                source_type=source_type,
                search=search,
                limit=limit,
            ),
            asyncio.to_thread(
                self.repository.summarize_documents,
                user_id=context.user_id,
            ),
        )

    async def get(self, context: RequestContext, doc_id: str) -> dict[str, Any]:
        document = await asyncio.to_thread(
            self.repository.get_document,
            user_id=context.user_id,
            doc_id=doc_id,
        )
        if document is None:
            raise NotFoundError("Knowledge document not found")
        return document

    async def list_bases(
        self, context: RequestContext, *, status: str | None = None
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.repository.list_knowledge_bases,
            user_id=context.user_id,
            status=status,
        )

    async def create_base(
        self, context: RequestContext, *, name: str, description: str = ""
    ) -> dict[str, Any]:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValidationError("Knowledge base name must not be blank")
        item = await asyncio.to_thread(
            self.repository.create_knowledge_base,
            knowledge_base_id=f"kb_{uuid.uuid4().hex}",
            user_id=context.user_id,
            name=normalized_name,
            description=description.strip(),
            actor_id=context.principal.subject,
        )
        if item is None:
            raise ConflictError("A Knowledge base with this name already exists")
        return item

    async def update_base(
        self,
        context: RequestContext,
        knowledge_base_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if name is None and description is None and status is None:
            raise ValidationError("At least one Knowledge base field is required")
        normalized_name = name.strip() if name is not None else None
        if name is not None and not normalized_name:
            raise ValidationError("Knowledge base name must not be blank")
        item, outcome = await asyncio.to_thread(
            self.repository.update_knowledge_base,
            user_id=context.user_id,
            knowledge_base_id=knowledge_base_id,
            actor_id=context.principal.subject,
            name=normalized_name,
            description=description.strip() if description is not None else None,
            status=status,
        )
        if outcome == "not_found":
            raise NotFoundError("Knowledge base not found")
        if outcome == "name_conflict":
            raise ConflictError("A Knowledge base with this name already exists")
        assert item is not None
        return item

    async def delete_base(
        self, context: RequestContext, knowledge_base_id: str
    ) -> dict[str, Any]:
        item = await asyncio.to_thread(
            self.repository.delete_knowledge_base,
            user_id=context.user_id,
            knowledge_base_id=knowledge_base_id,
            actor_id=context.principal.subject,
        )
        if item is None:
            raise NotFoundError("Knowledge base not found")
        return item

    async def bind_document(
        self, context: RequestContext, knowledge_base_id: str, doc_id: str
    ) -> bool:
        outcome = await asyncio.to_thread(
            self.repository.bind_document,
            user_id=context.user_id,
            knowledge_base_id=knowledge_base_id,
            doc_id=doc_id,
            actor_id=context.principal.subject,
        )
        if outcome == "base_not_found":
            raise NotFoundError("Knowledge base not found")
        if outcome == "document_not_found":
            raise NotFoundError("Knowledge document not found")
        return outcome == "bound"

    async def unbind_document(
        self, context: RequestContext, knowledge_base_id: str, doc_id: str
    ) -> bool:
        outcome = await asyncio.to_thread(
            self.repository.unbind_document,
            user_id=context.user_id,
            knowledge_base_id=knowledge_base_id,
            doc_id=doc_id,
            actor_id=context.principal.subject,
        )
        if outcome == "base_not_found":
            raise NotFoundError("Knowledge base not found")
        return outcome == "unbound"

    async def delete(self, context: RequestContext, doc_id: str) -> dict[str, Any]:
        document = await asyncio.to_thread(
            self.repository.delete_document,
            user_id=context.user_id,
            doc_id=doc_id,
            actor_id=context.principal.subject,
        )
        if document is None:
            raise NotFoundError("Knowledge document not found")
        return document
