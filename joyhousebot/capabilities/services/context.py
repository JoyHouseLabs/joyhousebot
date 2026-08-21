"""User-scoped Memory and Knowledge port."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from typing import Any

from joyhousebot.contracts.capabilities import CapabilityContext
from joyhousebot.domain.memory_policy import EffectiveMemoryPolicy
from joyhousebot.services.memory import MemoryStore, MemoryWriteController
from joyhousebot.services.retrieval.embedding_execution import (
    execute_embedding_profile,
)
from joyhousebot.services.retrieval.knowledge_repository import KnowledgeRepository

_logger = logging.getLogger(__name__)


class ContextPort:
    def __init__(
        self, runtime_store: Any | None, *, embedding_provider_resolver: Any = None
    ) -> None:
        self._store = runtime_store
        self._embedding_provider_resolver = embedding_provider_resolver
        self._rerank_executor: Any = None

    def set_rerank_executor(self, executor: Any) -> None:
        """Install the Core dispatcher callback after registry composition."""
        self._rerank_executor = executor

    def _require_store(self) -> Any:
        if self._store is None:
            raise RuntimeError("durable Runtime store is unavailable")
        return self._store

    async def _resolve_embedding_profile(
        self,
        context: CapabilityContext,
        revision_id: str | None,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Resolve a frozen Profile without trusting caller-controlled metadata.

        Draft revisions are executable only inside the synthetic owner namespace
        of a currently running retrieval Eval for that exact target revision.
        """
        store = self._require_store()
        if not revision_id:
            return await asyncio.to_thread(store.get_published_embedding_profile), False
        profile = await asyncio.to_thread(
            store.get_embedding_profile_execution_revision,
            revision_id,
            allow_draft_evaluation=False,
        )
        if profile is not None:
            return profile, False
        eval_run_id, _ = await self._embedding_eval_scope(context, revision_id)
        if eval_run_id is None:
            return None, False
        profile = await asyncio.to_thread(
            store.get_embedding_profile_execution_revision,
            revision_id,
            allow_draft_evaluation=True,
        )
        return profile, profile is not None

    async def _embedding_eval_scope(
        self,
        context: CapabilityContext,
        revision_id: str,
    ) -> tuple[str | None, str | None]:
        """Return validated Eval correlation, never raw caller metadata."""
        store = self._require_store()
        eval_run_id = str(context.metadata.get("eval_run_id") or "").strip()
        eval_case_id = str(context.metadata.get("eval_case_id") or "").strip()
        if (
            not eval_run_id
            or not eval_case_id
            or context.user_id != f"eval:{eval_run_id}"
        ):
            return None, None
        eval_run = await asyncio.to_thread(store.get_eval_run, eval_run_id)
        if (
            eval_run is None
            or str(eval_run.get("status")) != "running"
            or str(eval_run.get("target_type")) != "embedding_profile"
            or str(eval_run.get("target_revision_id")) != revision_id
        ):
            return None, None
        suite = await asyncio.to_thread(
            store.get_eval_suite,
            str(eval_run["suite_id"]),
            int(eval_run["suite_version"]),
        )
        if suite is None or eval_case_id not in {
            str(item.get("case_id") or "") for item in suite.get("cases") or []
        }:
            return None, None
        return eval_run_id, eval_case_id

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
            frozen_profile = str(
                context.metadata.get("embedding_profile_id") or ""
            ).strip()
            profile, draft_evaluation = await self._resolve_embedding_profile(
                context,
                frozen_profile or None,
            )
            if frozen_profile and profile is None:
                raise ValueError("frozen embedding profile revision is not executable")
            if profile is not None:
                try:
                    eval_run_id, eval_case_id = await self._embedding_eval_scope(
                        context,
                        profile["revision_id"],
                    )
                    repository = getattr(store, "_knowledge_repository", None)
                    if repository is None:
                        repository = KnowledgeRepository(store)
                        store._knowledge_repository = repository
                    execution = await execute_embedding_profile(
                        store=store,
                        repository=repository,
                        provider_resolver=self._embedding_provider_resolver,
                        profile=profile,
                        texts=[query],
                        user_id=context.user_id,
                        doc_id=None,
                        revision_id=None,
                        operation_type="query",
                        run_id=context.run_id,
                        task_id=context.task_id,
                        eval_run_id=eval_run_id,
                        eval_case_id=eval_case_id,
                    )
                    query_embedding = execution.embeddings[0]
                    if profile["configuration"]["normalization"] == "l2":
                        query_embedding = self._l2_normalize(query_embedding)
                    hits = await asyncio.to_thread(
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
                    if draft_evaluation:
                        raise
                    # Retrieval stays available when the optional embedding path is unhealthy.
                    _logger.warning(
                        "hybrid Knowledge retrieval failed; using lexical fallback"
                    )
                else:
                    return await self._apply_rerank(
                        context, query=query, hits=hits, scope=scope
                    )
        hits = await search_async(
            query=query,
            top_k=top_k,
            source_type=source_type,
            collection_ref=collection_ref,
            scope=scope,
            memory_scope_key=context.memory_scope,
            runtime_store=self._require_store(),
            user_id=context.user_id,
        )
        return await self._apply_rerank(context, query=query, hits=hits, scope=scope)

    async def _apply_rerank(
        self,
        context: CapabilityContext,
        *,
        query: str,
        hits: list[dict[str, Any]],
        scope: str,
    ) -> list[dict[str, Any]]:
        """Optionally rerank authorized hits through an exact Agent policy ref.

        The reference comes from the Agent's frozen memory/retrieval policy,
        never from the model tool-call input. The nested call stays in the
        Capability Dispatcher and therefore writes normal invocation/Trace
        evidence. A profile may opt into fail-closed behavior; otherwise the
        original retrieval ordering remains available and the fallback reason
        is returned to the calling capability.
        """
        policy = self._rerank_policy(context.memory_policy, scope=scope)
        if policy is None or not hits:
            return hits
        if self._rerank_executor is None:
            return self._rerank_fallback(
                context, hits, "rerank_executor_unavailable", policy
            )
        candidates = [
            {
                "candidate_id": self._candidate_id(hit),
                "text": str(hit.get("content") or "")[:20_000],
            }
            for hit in hits[: policy["candidate_limit"]]
        ]
        by_id = {self._candidate_id(hit): hit for hit in hits}
        try:
            result = await self._rerank_executor(
                context,
                capability_id=policy["capability_id"],
                version=policy["version"],
                input={
                    "query": query,
                    "candidates": candidates,
                    "top_k": min(len(candidates), policy["top_k"]),
                },
            )
            if not result.ok:
                message = str(
                    getattr(getattr(result, "error", None), "code", "rerank_failed")
                )
                return self._rerank_fallback(context, hits, message, policy)
            output = dict(getattr(result, "data", {}) or {}).get("output") or {}
            ranked = list(dict(output).get("ranked") or [])
            ordered: list[dict[str, Any]] = []
            for item in ranked:
                candidate_id = str(dict(item).get("candidate_id") or "")
                hit = by_id.pop(candidate_id, None)
                if hit is None:
                    continue
                value = dict(hit)
                value["rerank_score"] = float(dict(item).get("score") or 0.0)
                value["rerank_rank"] = int(dict(item).get("rank") or len(ordered) + 1)
                ordered.append(value)
            ordered.extend(by_id.values())
            context.metadata["retrieval_rerank"] = {
                "applied": True,
                "capability_id": policy["capability_id"],
                "version": policy["version"],
                "fallback": False,
            }
            return ordered
        except Exception as exc:  # defensive: retrieval policy decides degradation
            return self._rerank_fallback(context, hits, type(exc).__name__, policy)

    @staticmethod
    def _candidate_id(hit: dict[str, Any]) -> str:
        return ":".join(
            (
                str(hit.get("doc_id") or ""),
                str(hit.get("revision_id") or ""),
                str(hit.get("chunk_index") or 0),
            )
        )

    @staticmethod
    def _rerank_policy(
        memory_policy: dict[str, Any], *, scope: str
    ) -> dict[str, Any] | None:
        if scope != "knowledge":
            return None
        retrieval = dict(EffectiveMemoryPolicy.from_dict(memory_policy).retrieval or {})
        raw = retrieval.get("rerank")
        if not isinstance(raw, dict) or not raw.get("enabled"):
            return None
        capability_id = str(raw.get("capability_id") or "retrieval.rerank").strip()
        version = str(raw.get("version") or "").strip()
        if capability_id != "retrieval.rerank" or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,128}", version
        ):
            raise ValueError("invalid frozen retrieval rerank policy")
        candidate_limit = max(1, min(50, int(raw.get("candidate_limit") or 20)))
        top_k = max(1, min(candidate_limit, int(raw.get("top_k") or candidate_limit)))
        fallback = str(raw.get("failure_mode") or "fallback")
        if fallback not in {"fallback", "fail_closed"}:
            raise ValueError("rerank failure_mode must be fallback or fail_closed")
        return {
            "capability_id": capability_id,
            "version": version,
            "candidate_limit": candidate_limit,
            "top_k": top_k,
            "failure_mode": fallback,
        }

    @staticmethod
    def _rerank_fallback(
        context: CapabilityContext,
        hits: list[dict[str, Any]],
        reason: str,
        policy: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if policy["failure_mode"] == "fail_closed":
            raise RuntimeError(f"configured rerank failed: {reason}")
        context.metadata["retrieval_rerank"] = {
            "applied": False,
            "capability_id": policy["capability_id"],
            "version": policy["version"],
            "fallback": True,
            "reason": reason,
        }
        for hit in hits:
            hit["rerank_fallback"] = reason
        return hits

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
        doc_id = self._knowledge_doc_id(
            context.user_id,
            source_system,
            resolved_source_id,
            app_installation_id=context.app_installation_id,
        )
        embedding_profile, draft_evaluation = await self._resolve_embedding_profile(
            context,
            embedding_profile_id,
        ) if embedding_profile_id else (None, False)
        eval_run_id, eval_case_id = (
            await self._embedding_eval_scope(context, embedding_profile["revision_id"])
            if embedding_profile is not None
            else (None, None)
        )
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
            embedding_profile=embedding_profile,
            embedding_provider_resolver=self._embedding_provider_resolver,
            allow_draft_evaluation=draft_evaluation,
            task_id=context.task_id,
            eval_run_id=eval_run_id,
            eval_case_id=eval_case_id,
            app_installation_id=context.app_installation_id,
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
        doc_id = self._knowledge_doc_id(
            context.user_id,
            source_system,
            resolved_source_id,
            app_installation_id=context.app_installation_id,
        )
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
    def _knowledge_doc_id(
        user_id: str,
        source_system: str,
        source_id: str,
        *,
        app_installation_id: str | None = None,
    ) -> str:
        # App documents hash under their installation identity: the same
        # source_id can exist in the personal library and in an App library
        # without colliding doc_ids.
        namespace_owner = app_installation_id or user_id
        return hashlib.sha256(
            f"{namespace_owner}:{source_system}:{source_id}".encode()
        ).hexdigest()[:24]

    @staticmethod
    async def _index_knowledge_revision(
        repository: KnowledgeRepository,
        *,
        actor_id: str,
        embedding_profile: dict[str, Any] | None,
        embedding_provider_resolver: Any,
        allow_draft_evaluation: bool = False,
        task_id: str | None = None,
        eval_run_id: str | None = None,
        eval_case_id: str | None = None,
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
                texts = [str(chunk.get("text") or "") for chunk in kwargs["chunks"]]
                execution = await execute_embedding_profile(
                    store=repository.store,
                    repository=repository,
                    provider_resolver=embedding_provider_resolver,
                    profile=embedding_profile,
                    texts=texts,
                    user_id=kwargs["user_id"],
                    doc_id=kwargs["doc_id"],
                    revision_id=revision_id,
                    operation_type="index",
                    run_id=kwargs.get("run_id"),
                    task_id=task_id,
                    eval_run_id=eval_run_id,
                    eval_case_id=eval_case_id,
                )
                embeddings = execution.embeddings
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
                    allow_draft_evaluation=allow_draft_evaluation,
                )
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
