"""User-scoped Memory and Knowledge port."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from joyhousebot.contracts.capabilities import CapabilityContext
from joyhousebot.domain.memory_policy import EffectiveMemoryPolicy
from joyhousebot.services.memory import MemoryStore, MemoryWriteController
from joyhousebot.services.retrieval.knowledge_repository import KnowledgeRepository


class ContextPort:
    def __init__(self, runtime_store: Any | None) -> None:
        self._store = runtime_store

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
        scope: str = "knowledge",
    ) -> list[dict[str, Any]]:
        if scope == "memory" and not EffectiveMemoryPolicy.from_dict(
            context.memory_policy
        ).can_read_tools:
            raise PermissionError("memory retrieval is disabled by the Agent memory policy")
        from joyhousebot.services.retrieval.adapter import search_async

        return await search_async(
            query=query,
            top_k=top_k,
            source_type=source_type,
            scope=scope,
            memory_scope_key=context.memory_scope,
            runtime_store=self._require_store(),
            user_id=context.user_id,
        )

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
    ) -> str:
        store = self._require_store()
        repository = getattr(store, "_knowledge_repository", None)
        if repository is None:
            repository = KnowledgeRepository(store)
            store._knowledge_repository = repository
        doc_id = hashlib.sha256(f"{context.user_id}:{source_url}".encode()).hexdigest()[:24]
        await asyncio.to_thread(
            repository.index_document,
            doc_id=doc_id,
            user_id=context.user_id,
            agent_id=context.agent_id,
            source_type=source_type,
            source_url=source_url,
            title=title,
            chunks=chunks,
            metadata=metadata or {},
        )
        return doc_id


__all__ = ["ContextPort"]
