"""Persistence records for source-level model context manifests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ContextManifestEntryRecord:
    entry_id: str
    ordinal: int
    source_kind: str
    source_id: str
    owner_scope: str
    classification: str
    authority: str
    freshness: str
    content_hash: str
    estimated_tokens: int
    priority: int
    included: bool
    included_reason: str | None
    excluded_reason: str | None
    citation_id: str | None
    redaction_policy: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ContextManifestRecord:
    manifest_id: str
    turn_id: str
    run_id: str
    task_id: str | None
    scope: str
    turn_index: int
    owner_scope: str
    request_hash: str
    manifest_hash: str
    budget_tokens: int | None
    budget_strategy: str
    estimated_tokens: int
    included_tokens: int
    excluded_tokens: int
    worker_id: str | None
    run_lease_version: int | None
    task_lease_version: int | None
    created_at: str
    entries: tuple[ContextManifestEntryRecord, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
