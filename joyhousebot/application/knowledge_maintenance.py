"""Governed Knowledge projection maintenance use cases."""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.application.context import RequestContext
from joyhousebot.application.errors import ConflictError, NotFoundError, ValidationError
from joyhousebot.capabilities.services.context import ContextPort
from joyhousebot.services.retrieval.embedding_execution import execute_embedding_profile
from joyhousebot.services.retrieval.knowledge_repository import KnowledgeRepository


class KnowledgeMaintenanceService:
    def __init__(
        self,
        store: Any,
        *,
        embedding_provider_resolver: Any = None,
    ) -> None:
        self.store = store
        self.repository = KnowledgeRepository(store)
        self.embedding_provider_resolver = embedding_provider_resolver

    async def enqueue_reembedding(
        self,
        context: RequestContext,
        *,
        embedding_profile_id: str,
        knowledge_base_id: str | None,
        doc_id: str | None,
    ) -> dict[str, Any]:
        if not context.idempotency_key:
            raise ValidationError("Knowledge re-embedding requires an Idempotency-Key header")
        profile = await asyncio.to_thread(
            self.store.get_published_embedding_profile,
            profile_id=embedding_profile_id,
        )
        if profile is None:
            raise ValidationError("Published embedding profile not found")
        try:
            return await asyncio.to_thread(
                self.repository.enqueue_reembedding_job,
                user_id=context.user_id,
                embedding_profile_id=profile["revision_id"],
                requested_by=context.principal.subject,
                idempotency_key=context.idempotency_key,
                knowledge_base_id=knowledge_base_id,
                doc_id=doc_id,
            )
        except ValueError as exc:
            if "not found" in str(exc).lower():
                raise NotFoundError(str(exc)) from exc
            raise ConflictError(str(exc)) from exc

    async def list_jobs(
        self, context: RequestContext, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.repository.list_reembedding_jobs,
            user_id=context.user_id,
            limit=limit,
        )

    async def get_job(self, context: RequestContext, job_id: str) -> dict[str, Any]:
        value = await asyncio.to_thread(
            self.repository.get_reembedding_job,
            user_id=context.user_id,
            job_id=job_id,
        )
        if value is None:
            raise NotFoundError("Knowledge re-embedding job not found")
        return value

    async def cancel_job(self, context: RequestContext, job_id: str) -> None:
        cancelled = await asyncio.to_thread(
            self.repository.cancel_reembedding_job,
            user_id=context.user_id,
            job_id=job_id,
            actor_id=context.principal.subject,
        )
        if not cancelled:
            current = await asyncio.to_thread(
                self.repository.get_reembedding_job,
                user_id=context.user_id,
                job_id=job_id,
            )
            if current is None:
                raise NotFoundError("Knowledge re-embedding job not found")
            raise ConflictError("Knowledge re-embedding job is already terminal")

    async def process_item(self, item: dict[str, Any], *, worker_id: str) -> None:
        if self.embedding_provider_resolver is None:
            raise RuntimeError("embedding provider resolver is unavailable")
        profile = await asyncio.to_thread(
            self.store.get_published_embedding_profile,
            profile_id=item["embedding_profile_id"],
            allow_retired=True,
        )
        if profile is None:
            raise RuntimeError("frozen embedding profile revision is unavailable")
        execution = await execute_embedding_profile(
            store=self.store,
            repository=self.repository,
            provider_resolver=self.embedding_provider_resolver,
            profile=profile,
            texts=list(item["chunks"]),
            user_id=item["user_id"],
            doc_id=item["doc_id"],
            revision_id=item["revision_id"],
            operation_type="reembed",
            run_id=None,
            task_id=None,
            eval_run_id=None,
            eval_case_id=None,
        )
        embeddings = execution.embeddings
        if profile["configuration"]["normalization"] == "l2":
            embeddings = [ContextPort._l2_normalize(value) for value in embeddings]
        await asyncio.to_thread(
            self.repository.store_reembedded_revision,
            job_id=item["job_id"],
            user_id=item["user_id"],
            doc_id=item["doc_id"],
            revision_id=item["revision_id"],
            embedding_profile_id=item["embedding_profile_id"],
            embeddings=embeddings,
            actor_id=f"worker:{worker_id}",
            worker_id=worker_id,
            lease_version=int(item["lease_version"]),
        )


__all__ = ["KnowledgeMaintenanceService"]
