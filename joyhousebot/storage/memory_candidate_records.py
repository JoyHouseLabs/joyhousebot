"""Typed records for governed long-term Memory updates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class MemoryCandidateRecord:
    candidate_id: str
    user_id: str
    agent_id: str
    scope_key: str
    document_path: str
    layer: str
    operation: str
    content: str
    content_hash: str
    base_document_version: int
    base_content_hash: str
    source_run_id: str | None
    source_task_id: str | None
    source_turn_id: str | None
    source_action_id: str | None
    source_kind: str
    source_fingerprint: str
    fact_type: str
    confidence: float | None
    data_classification: str
    supersedes: list[str]
    evidence_refs: list[dict[str, Any]]
    valid_until: str | None
    policy_snapshot: dict[str, Any]
    merge_options: dict[str, Any]
    status: str
    resolution: str | None
    resolution_note: str | None
    resolved_by: str | None
    created_at: str
    expires_at: str
    resolved_at: str | None
    updated_at: str
