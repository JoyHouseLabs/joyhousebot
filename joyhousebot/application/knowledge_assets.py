"""Owner-scoped use cases for durable Knowledge assets."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from joyhousebot.application.context import RequestContext
from joyhousebot.application.errors import ConflictError, NotFoundError, ValidationError
from joyhousebot.domain.capabilities.models import CapabilityRef
from joyhousebot.runtime.models import GraphTaskSpec, TaskGraphSpec
from joyhousebot.services.retrieval.knowledge_repository import KnowledgeRepository


class KnowledgeAssetService:
    """Expose the Runtime knowledge index through an owner control plane."""

    INDEX_AUTHORITY_PERMISSIONS = frozenset({"knowledge.write"})

    def __init__(self, store: Any, runtime: Any | None = None) -> None:
        self.repository = KnowledgeRepository(store)
        self.store = store
        self.runtime = runtime

    async def submit_index_request(self, context: RequestContext, snapshot: dict[str, Any]) -> Any:
        if self.runtime is None:
            raise ConflictError("Knowledge indexing Runtime is unavailable")
        if not context.idempotency_key:
            raise ValidationError("Knowledge indexing requires an Idempotency-Key header")
        definitions = await asyncio.to_thread(self.store.list_capability_definitions)
        definition = next(
            (
                item
                for item in definitions
                if (
                    item.ref.capability_id
                    if hasattr(item, "ref")
                    else item.get("ref", {}).get("capability_id")
                )
                == "knowledge.index"
                and (
                    item.ref.kind.value if hasattr(item, "ref") else item.get("ref", {}).get("kind")
                )
                in {"tool", "connector"}
            ),
            None,
        )
        if definition is None:
            raise ConflictError("Published knowledge.index capability is required before indexing")
        reference = (
            definition.ref
            if hasattr(definition, "ref")
            else CapabilityRef.from_dict(dict(definition["ref"]))
        )
        required_permissions = {
            str(item).strip()
            for item in (
                getattr(definition, "permissions", ())
                if hasattr(definition, "permissions")
                else definition.get("permissions", ())
            )
            if str(item).strip()
        }
        unsupported_permissions = sorted(
            required_permissions - self.INDEX_AUTHORITY_PERMISSIONS
        )
        if unsupported_permissions:
            raise ConflictError(
                "knowledge.index requests unsupported authority: "
                + ", ".join(unsupported_permissions)
            )
        source_id = str(snapshot["source_id"])
        source_version = str(snapshot["source_version"])
        profile_id = str(snapshot.get("index_profile_id") or "lexical-v1")
        embedding_profile_id = snapshot.get("embedding_profile_id")
        if embedding_profile_id:
            embedding_profile = await asyncio.to_thread(
                self.store.get_published_embedding_profile,
                profile_id=str(embedding_profile_id),
            )
            if embedding_profile is None:
                raise ValidationError("Published embedding profile not found")
            snapshot["embedding_profile_id"] = embedding_profile["revision_id"]
        profile = await asyncio.to_thread(self.store.get_agent_profile)
        if profile is None:
            raise ConflictError("No active published default Agent exists")
        input_asset_ids = [
            str(item.get("asset_id") or "")
            for item in list(snapshot.get("attachments") or [])
            if str(item.get("reference_kind") or "") == "runtime_input"
        ]
        spec = TaskGraphSpec(
            goal=f"Index knowledge source {source_id} version {source_version}",
            user_id=context.user_id,
            session_id=f"knowledge-index:{source_id}"[:128],
            agent_id=profile.definition.agent_id,
            max_concurrent=1,
            fail_fast=True,
            aggregate=False,
            idempotency_key=context.idempotency_key,
            request_id=context.request_id,
            tracker_id=context.tracker_id,
            traceparent=context.traceparent,
            tracestate=context.tracestate,
            input_asset_ids=input_asset_ids,
            authority_permissions=sorted(required_permissions),
            metadata={
                "purpose": "knowledge.index",
                "source_system": snapshot["source_system"],
                "source_id": source_id,
                "source_version": source_version,
                "index_profile_id": profile_id,
                "embedding_profile_id": snapshot.get("embedding_profile_id"),
            },
            tasks=[
                GraphTaskSpec(
                    id="index",
                    name="Parse and index source snapshot",
                    prompt="",
                    node_type="capability",
                    capability=reference,
                    capability_input=snapshot,
                    max_attempts=3,
                    timeout_seconds=600,
                    metadata={
                        "source_system": snapshot["source_system"],
                        "source_id": source_id,
                        "source_version": source_version,
                    },
                )
            ],
        )
        try:
            return await self.runtime.submit_graph(spec)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

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

    async def health(self, context: RequestContext, *, window_days: int) -> dict[str, Any]:
        since_ms = int((time.time() - (window_days * 86400)) * 1000)
        return await asyncio.to_thread(
            self.repository.index_health,
            user_id=context.user_id,
            since_ms=since_ms,
        )

    async def get_source_state(
        self, context: RequestContext, *, source_system: str, source_id: str
    ) -> dict[str, Any]:
        document = await asyncio.to_thread(
            self.repository.get_document_by_source,
            user_id=context.user_id,
            source_system=source_system,
            source_id=source_id,
        )
        if document is None:
            raise NotFoundError("Knowledge source not found")
        revisions = await asyncio.to_thread(
            self.repository.list_index_revisions,
            user_id=context.user_id,
            doc_id=document["doc_id"],
        )
        return {"document": document, "revisions": revisions}

    async def search(
        self,
        context: RequestContext,
        *,
        query: str,
        top_k: int,
        knowledge_base_id: str | None = None,
        source_type: str | None = None,
        doc_id: str | None = None,
        collection_ref: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValidationError("Knowledge search query must not be blank")
        if (
            knowledge_base_id
            and await asyncio.to_thread(
                self.repository.get_knowledge_base,
                user_id=context.user_id,
                knowledge_base_id=knowledge_base_id,
            )
            is None
        ):
            raise NotFoundError("Knowledge base not found")
        return await asyncio.to_thread(
            self.repository.search,
            user_id=context.user_id,
            query=normalized_query,
            top_k=top_k,
            knowledge_base_id=knowledge_base_id,
            source_type=source_type,
            doc_id=doc_id,
            collection_ref=collection_ref,
        )

    async def list_revisions(self, context: RequestContext, doc_id: str) -> list[dict[str, Any]]:
        if (
            await asyncio.to_thread(
                self.repository.get_document, user_id=context.user_id, doc_id=doc_id
            )
            is None
        ):
            raise NotFoundError("Knowledge document not found")
        return await asyncio.to_thread(
            self.repository.list_index_revisions,
            user_id=context.user_id,
            doc_id=doc_id,
        )

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

    async def delete_base(self, context: RequestContext, knowledge_base_id: str) -> dict[str, Any]:
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
