"""Policy-aware write boundary for durable Agent Memory."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from joyhousebot.domain.memory_policy import EffectiveMemoryPolicy
from joyhousebot.services.memory.store import MemoryStore

_DEFAULT_CANDIDATE_TTL_SECONDS = 30 * 24 * 3600


@dataclass(frozen=True, slots=True)
class MemoryWriteReceipt:
    mode: str
    candidate_id: str | None = None
    created: bool = False


class MemoryWriteController:
    """Route an approved layer write to direct storage or the candidate inbox."""

    def __init__(
        self,
        runtime_store: Any,
        *,
        scope_key: str,
        policy: EffectiveMemoryPolicy,
        context: Any,
    ) -> None:
        canonical_scope = f"user:{context.user_id}:agent:{context.agent_id}"
        if scope_key.startswith("user:") and scope_key != canonical_scope:
            raise PermissionError("Memory scope does not match the authenticated Run owner")
        self.runtime_store = runtime_store
        self.memory = MemoryStore(runtime_store, scope_key=scope_key)
        self.scope_key = scope_key
        self.policy = policy
        self.context = context

    def replace(
        self,
        document_path: str,
        content: str,
        *,
        source_kind: str,
        source_fingerprint: str = "",
        fact_type: str | None = None,
        confidence: float | None = None,
        supersedes: list[str] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        valid_for_seconds: int | None = None,
    ) -> MemoryWriteReceipt:
        return self._write(
            document_path,
            content,
            operation="replace",
            source_kind=source_kind,
            source_fingerprint=source_fingerprint,
            fact_type=fact_type,
            confidence=confidence,
            supersedes=supersedes,
            evidence_refs=evidence_refs,
            valid_for_seconds=valid_for_seconds,
        )

    def append(
        self,
        document_path: str,
        content: str,
        *,
        source_kind: str,
        source_fingerprint: str = "",
        max_entries: int = 0,
        fact_type: str | None = None,
        confidence: float | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        valid_for_seconds: int | None = None,
    ) -> MemoryWriteReceipt:
        suffix = content.rstrip() + "\n\n"
        return self._write(
            document_path,
            suffix,
            operation="append",
            source_kind=source_kind,
            source_fingerprint=source_fingerprint,
            merge_options={"max_entries": max(0, int(max_entries))},
            fact_type=fact_type,
            confidence=confidence,
            evidence_refs=evidence_refs,
            valid_for_seconds=valid_for_seconds,
        )

    def _write(
        self,
        document_path: str,
        content: str,
        *,
        operation: str,
        source_kind: str,
        source_fingerprint: str,
        merge_options: dict[str, Any] | None = None,
        fact_type: str | None,
        confidence: float | None,
        supersedes: list[str] | None = None,
        evidence_refs: list[dict[str, Any]] | None,
        valid_for_seconds: int | None,
    ) -> MemoryWriteReceipt:
        clean_path = self.memory._clean_path(document_path)
        if not clean_path:
            raise ValueError("invalid Memory document path")
        if not self.policy.allows_path(clean_path, "write"):
            raise PermissionError(f"Memory write is disabled for {clean_path}")
        if self.policy.write_mode == "direct":
            if operation == "replace":
                self.memory.write_relative(clean_path, content)
            else:
                self.memory.repository.append(self.scope_key, clean_path, content)
                count = int((merge_options or {}).get("max_entries") or 0)
                if count > 0:
                    self.memory._trim_history_to_last_n(count)
            return MemoryWriteReceipt(mode="direct")
        if self.policy.write_mode != "candidate":
            raise PermissionError("Memory writes are disabled by the Agent policy")

        content_hash = sha256(content.encode("utf-8")).hexdigest()
        source_key = (
            self.context.action_id
            if getattr(self.context, "action_id", None)
            else source_fingerprint
            or ":".join(
                item
                for item in (
                    self.context.run_id,
                    self.context.task_id,
                    getattr(self.context, "turn_id", None),
                    source_kind,
                )
                if item
            )
        )
        fingerprint = sha256(str(source_key).encode("utf-8")).hexdigest()
        identity = "\0".join(
            (
                self.context.user_id,
                self.context.agent_id,
                self.scope_key,
                clean_path,
                operation,
                content_hash,
                fingerprint,
            )
        )
        candidate_id = f"memcand_{sha256(identity.encode('utf-8')).hexdigest()}"
        retrieval = self.policy.retrieval
        try:
            ttl = int(
                retrieval.get("candidate_ttl_seconds")
                or _DEFAULT_CANDIDATE_TTL_SECONDS
            )
        except (TypeError, ValueError):
            ttl = _DEFAULT_CANDIDATE_TTL_SECONDS
        ttl = max(3600, min(365 * 24 * 3600, ttl))
        default_confidence = retrieval.get("candidate_confidence")
        resolved_confidence = confidence if confidence is not None else default_confidence
        if resolved_confidence is not None:
            try:
                resolved_confidence = max(0.0, min(1.0, float(resolved_confidence)))
            except (TypeError, ValueError):
                resolved_confidence = None
        resolved_valid_for = (
            valid_for_seconds
            if valid_for_seconds is not None
            else retrieval.get("candidate_valid_for_seconds")
        )
        if resolved_valid_for is not None:
            try:
                resolved_valid_for = max(
                    3600, min(10 * 365 * 24 * 3600, int(resolved_valid_for))
                )
            except (TypeError, ValueError):
                resolved_valid_for = None
        classification = str(
            retrieval.get("candidate_data_classification") or "confidential"
        )
        if classification not in {"public", "internal", "confidential", "restricted"}:
            classification = "confidential"
        record, created = self.runtime_store.create_memory_candidate(
            candidate_id=candidate_id,
            user_id=self.context.user_id,
            agent_id=self.context.agent_id,
            scope_key=self.scope_key,
            document_path=clean_path,
            layer=self.policy.layer_for_path(clean_path),
            operation=operation,
            content=content,
            content_hash=content_hash,
            source_run_id=self.context.run_id or None,
            source_task_id=self.context.task_id,
            source_turn_id=getattr(self.context, "turn_id", None),
            source_action_id=getattr(self.context, "action_id", None),
            source_kind=source_kind,
            source_fingerprint=fingerprint,
            fact_type=fact_type or self.policy.layer_for_path(clean_path),
            confidence=resolved_confidence,
            data_classification=classification,
            supersedes=supersedes or [],
            evidence_refs=evidence_refs or [],
            valid_for_seconds=resolved_valid_for,
            policy_snapshot=self.policy.to_dict(),
            merge_options=merge_options or {},
            expires_in_seconds=ttl,
        )
        return MemoryWriteReceipt(
            mode="candidate", candidate_id=record.candidate_id, created=created
        )
