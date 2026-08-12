"""User-scoped Memory and Knowledge port."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from typing import Any

from joyhousebot.contracts.capabilities import CapabilityContext
from joyhousebot.domain.memory_policy import EffectiveMemoryPolicy
from joyhousebot.services.memory import MemoryStore, MemoryWriteController
from joyhousebot.services.retrieval.knowledge_repository import KnowledgeRepository

_logger = logging.getLogger(__name__)


class ContextPort:
    def __init__(
        self, runtime_store: Any | None, *, embedding_provider_resolver: Any = None
    ) -> None:
        self._store = runtime_store
        self._embedding_provider_resolver = embedding_provider_resolver

    def _require_store(self) -> Any:
        if self._store is None:
            raise RuntimeError("durable Runtime store is unavailable")
        return self._store

    @staticmethod
    def _memory_scope(context: CapabilityContext) -> str:
        if not context.memory_scope:
            raise ValueError("run memory scope is required")
        canonical = f"user:{context.user_id}:agent:{context.agent_id or 'default'}"
        if context.memory_scope.startswith("user:") and context.memory_scope != canonical:
            raise PermissionError("memory scope does not match the authenticated Run owner")
        return context.memory_scope

    async def search(
        self,
        context: CapabilityContext,
        *,
        query: str,
        top_k: int = 10,
        source_type: str | None = None,
        collection_ref: str | None = None,
        scope: str = "knowledge",
    ) -> list[dict[str, Any]]:
        if scope == "memory" and not EffectiveMemoryPolicy.from_dict(
            context.memory_policy
        ).can_read_tools:
            raise PermissionError("memory retrieval is disabled by the Agent memory policy")
        from joyhousebot.services.retrieval.adapter import search_async

        if scope == "knowledge" and self._embedding_provider_resolver is not None:
            store = self._require_store()
            profile = await asyncio.to_thread(store.get_published_embedding_profile)
            if profile is not None:
                provider = None
                try:
                    provider = self._embedding_provider_resolver(profile["configuration"])
                    if asyncio.iscoroutine(provider):
                        provider = await provider
                    response = await provider.embed(
                        [query],
                        model=profile["configuration"]["model_id"],
                        dimensions=int(profile["configuration"]["dimensions"]),
                    )
                    query_embedding = response.embeddings[0]
                    if profile["configuration"]["normalization"] == "l2":
                        query_embedding = self._l2_normalize(query_embedding)
                    repository = getattr(store, "_knowledge_repository", None)
                    if repository is None:
                        repository = KnowledgeRepository(store)
                        store._knowledge_repository = repository
                    return await asyncio.to_thread(
                        repository.search_hybrid,
                        user_id=context.user_id,
                        query=query,
                        query_embedding=query_embedding,
                        embedding_profile_id=profile["revision_id"],
                        top_k=top_k,
                        source_type=source_type,
                        collection_ref=collection_ref,
                    )
                except Exception:
                    # Retrieval stays available when the optional embedding path is unhealthy.
                    _logger.warning(
                        "hybrid Knowledge retrieval failed; using lexical fallback"
                    )
                finally:
                    close = getattr(provider, "close", None) if provider is not None else None
                    if callable(close):
                        closed = close()
                        if asyncio.iscoroutine(closed):
                            await closed
        return await search_async(
            query=query,
            top_k=top_k,
            source_type=source_type,
            collection_ref=collection_ref,
            scope=scope,
            memory_scope_key=context.memory_scope,
            runtime_store=self._require_store(),
            user_id=context.user_id,
        )

    async def read_input_asset(
        self,
        context: CapabilityContext,
        *,
        asset_id: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        """Read only an immutable file explicitly frozen into this Run."""
        store = self._require_store()
        record = await asyncio.to_thread(
            store.get_bound_input_asset,
            asset_id,
            run_id=context.run_id,
            expected_user_id=context.user_id,
        )
        if record is None:
            raise PermissionError("input asset is not bound to the current Run")
        configured_limit = int(getattr(store, "input_asset_max_bytes", max_bytes))
        read_limit = min(max(1, int(max_bytes)), configured_limit)
        object_store = getattr(store, "input_asset_store", None)
        if object_store is None:
            raise RuntimeError("Runtime Input Asset storage is unavailable")
        body = await asyncio.to_thread(
            object_store.read_bytes, record.storage_uri, max_bytes=read_limit
        )
        audit_read = getattr(store, "audit_input_asset_read", None)
        if audit_read is not None:
            await asyncio.to_thread(
                audit_read,
                asset_id=asset_id,
                run_id=context.run_id,
                user_id=context.user_id,
            )
        return {
            "body": body,
            "asset_id": record.asset_id,
            "display_name": record.original_name,
            "media_type": record.media_type,
            "content_sha256": record.content_sha256,
            "byte_size": record.byte_size,
            "object_version": record.object_version,
        }

    async def read_memory(
        self,
        context: CapabilityContext,
        *,
        relative_path: str,
        start_line: int | None = None,
        num_lines: int | None = None,
    ) -> str:
        scope_key = self._memory_scope(context)
        policy = EffectiveMemoryPolicy.from_dict(context.memory_policy)
        if not policy.allows_path(relative_path, "read"):
            raise PermissionError("Agent memory policy denies this path")
        text = await asyncio.to_thread(
            MemoryStore(self._require_store(), scope_key).read_relative,
            relative_path,
        )
        if start_line is not None and num_lines is not None:
            lines = text.splitlines()
            start = min(max(0, start_line - 1), len(lines))
            text = "\n".join(lines[start : start + max(1, num_lines)])
        return text

    async def list_memory(
        self,
        context: CapabilityContext,
        *,
        relative_path: str = "",
    ) -> list[dict[str, Any]]:
        scope_key = self._memory_scope(context)
        policy = EffectiveMemoryPolicy.from_dict(context.memory_policy)
        if not policy.can_read_tools:
            raise PermissionError("memory listing is disabled by the Agent memory policy")
        items = await asyncio.to_thread(
            MemoryStore(self._require_store(), scope_key).list_relative,
            relative_path,
        )
        return [{"name": name, "is_directory": is_directory} for name, is_directory in items]

    async def write_memory(
        self,
        context: CapabilityContext,
        *,
        relative_path: str,
        content: str,
        source_kind: str,
    ) -> dict[str, Any]:
        scope_key = self._memory_scope(context)
        policy = EffectiveMemoryPolicy.from_dict(context.memory_policy)
        receipt = await asyncio.to_thread(
            MemoryWriteController(
                self._require_store(),
                scope_key=scope_key,
                policy=policy,
                context=context,
            ).replace,
            relative_path,
            content,
            source_kind=source_kind,
        )
        return {
            "mode": receipt.mode,
            "candidate_id": receipt.candidate_id,
            "created": receipt.created,
        }

    async def index_knowledge(
        self,
        context: CapabilityContext,
        *,
        source_type: str,
        source_url: str,
        title: str,
        chunks: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
        source_system: str = "runtime",
        source_id: str | None = None,
        source_version: str = "1",
        source_generation: int = 1,
        source_status: str = "active",
        index_profile_id: str = "lexical-v1",
        parser_id: str = "preparsed",
        parser_version: str = "1",
        chunker_id: str = "provided-chunks",
        chunker_version: str = "1",
        embedding_profile_id: str | None = None,
    ) -> str:
        store = self._require_store()
        repository = getattr(store, "_knowledge_repository", None)
        if repository is None:
            repository = KnowledgeRepository(store)
            store._knowledge_repository = repository
        resolved_source_id = source_id or source_url or title
        doc_id = self._knowledge_doc_id(context.user_id, source_system, resolved_source_id)
        await self._index_knowledge_revision(
            repository,
            doc_id=doc_id,
            user_id=context.user_id,
            agent_id=context.agent_id,
            source_type=source_type,
            source_url=source_url,
            title=title,
            chunks=chunks,
            metadata=metadata or {},
            source_system=source_system,
            source_id=resolved_source_id,
            source_version=source_version,
            source_generation=source_generation,
            source_status=source_status,
            index_profile_id=index_profile_id,
            parser_id=parser_id,
            parser_version=parser_version,
            chunker_id=chunker_id,
            chunker_version=chunker_version,
            embedding_profile_id=embedding_profile_id,
            run_id=context.run_id,
            actor_id=f"worker:{context.agent_id or 'default'}",
            embedding_profile=(
                await asyncio.to_thread(
                    store.get_published_embedding_profile,
                    profile_id=embedding_profile_id,
                    allow_retired=True,
                )
                if embedding_profile_id
                else None
            ),
            embedding_provider_resolver=self._embedding_provider_resolver,
        )
        return doc_id

    async def fail_knowledge_index(
        self,
        context: CapabilityContext,
        *,
        source_type: str,
        source_url: str,
        title: str,
        error_code: str,
        error_message: str,
        metadata: dict[str, Any] | None = None,
        source_system: str = "runtime",
        source_id: str | None = None,
        source_version: str = "1",
        source_generation: int = 1,
        source_status: str = "active",
        index_profile_id: str = "lexical-v1",
        parser_id: str = "unresolved",
        parser_version: str = "1",
        chunker_id: str = "semantic-text-v1",
        chunker_version: str = "1",
        embedding_profile_id: str | None = None,
    ) -> str:
        """Persist a failed immutable attempt without replacing the active index."""
        store = self._require_store()
        repository = getattr(store, "_knowledge_repository", None)
        if repository is None:
            repository = KnowledgeRepository(store)
            store._knowledge_repository = repository
        resolved_source_id = source_id or source_url or title
        doc_id = self._knowledge_doc_id(context.user_id, source_system, resolved_source_id)
        await asyncio.to_thread(
            self._fail_knowledge_revision,
            repository,
            doc_id=doc_id,
            user_id=context.user_id,
            agent_id=context.agent_id,
            source_type=source_type,
            source_url=source_url,
            title=title,
            chunks=[],
            metadata=metadata or {},
            source_system=source_system,
            source_id=resolved_source_id,
            source_version=source_version,
            source_generation=source_generation,
            source_status=source_status,
            index_profile_id=index_profile_id,
            parser_id=parser_id,
            parser_version=parser_version,
            chunker_id=chunker_id,
            chunker_version=chunker_version,
            embedding_profile_id=embedding_profile_id,
            run_id=context.run_id,
            actor_id=f"worker:{context.agent_id or 'default'}",
            error_code=error_code,
            error_message=error_message,
        )
        return doc_id

    @staticmethod
    def _knowledge_doc_id(user_id: str, source_system: str, source_id: str) -> str:
        return hashlib.sha256(f"{user_id}:{source_system}:{source_id}".encode()).hexdigest()[:24]

    @staticmethod
    async def _index_knowledge_revision(
        repository: KnowledgeRepository,
        *,
        actor_id: str,
        embedding_profile: dict[str, Any] | None,
        embedding_provider_resolver: Any,
        **kwargs: Any,
    ) -> None:
        if kwargs.get("embedding_profile_id") and embedding_profile is None:
            raise ValueError("published embedding profile not found")
        revision_id = await asyncio.to_thread(repository.stage_index_revision, **kwargs)
        try:
            if embedding_profile is not None:
                if embedding_provider_resolver is None:
                    raise RuntimeError("embedding provider resolver is unavailable")
                configuration = dict(embedding_profile["configuration"])
                provider = embedding_provider_resolver(configuration)
                if asyncio.iscoroutine(provider):
                    provider = await provider
                try:
                    embeddings: list[list[float]] = []
                    texts = [str(chunk.get("text") or "") for chunk in kwargs["chunks"]]
                    batch_size = int(configuration["batch_size"])
                    for offset in range(0, len(texts), batch_size):
                        result = await provider.embed(
                            texts[offset : offset + batch_size],
                            model=configuration["model_id"],
                            dimensions=int(configuration["dimensions"]),
                        )
                        embeddings.extend(result.embeddings)
                    if configuration["normalization"] == "l2":
                        embeddings = [ContextPort._l2_normalize(item) for item in embeddings]
                    await asyncio.to_thread(
                        repository.stage_revision_embeddings,
                        user_id=kwargs["user_id"],
                        doc_id=kwargs["doc_id"],
                        revision_id=revision_id,
                        embedding_profile_id=embedding_profile["revision_id"],
                        embeddings=embeddings,
                        actor_id=actor_id,
                    )
                finally:
                    close = getattr(provider, "close", None)
                    if callable(close):
                        closed = close()
                        if asyncio.iscoroutine(closed):
                            await closed
            await asyncio.to_thread(
                repository.mark_index_revision_ready,
                user_id=kwargs["user_id"],
                doc_id=kwargs["doc_id"],
                revision_id=revision_id,
                actor_id=actor_id,
            )
            await asyncio.to_thread(
                repository.activate_index_revision,
                user_id=kwargs["user_id"],
                doc_id=kwargs["doc_id"],
                revision_id=revision_id,
                actor_id=actor_id,
            )
        except Exception as exc:
            await asyncio.to_thread(
                repository.fail_index_revision,
                user_id=kwargs["user_id"],
                doc_id=kwargs["doc_id"],
                revision_id=revision_id,
                actor_id=actor_id,
                error_code="INDEX_ACTIVATION_FAILED",
                error_message=str(exc),
            )
            raise

    @staticmethod
    def _l2_normalize(vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(float(value) ** 2 for value in vector))
        if norm <= 0:
            raise ValueError("embedding vector cannot be normalized")
        return [float(value) / norm for value in vector]

    @staticmethod
    def _fail_knowledge_revision(
        repository: KnowledgeRepository,
        *,
        actor_id: str,
        error_code: str,
        error_message: str,
        **kwargs: Any,
    ) -> None:
        revision_id = repository.stage_index_revision(**kwargs)
        repository.fail_index_revision(
            user_id=kwargs["user_id"],
            doc_id=kwargs["doc_id"],
            revision_id=revision_id,
            actor_id=actor_id,
            error_code=error_code,
            error_message=error_message,
        )


__all__ = ["ContextPort"]
