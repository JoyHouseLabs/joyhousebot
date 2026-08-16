"""Typed projections for durable GraphPatch audit records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GraphPatchRecord:
    patch_id: str
    run_id: str
    user_id: str
    base_revision_id: str
    result_revision_id: str
    proposer_type: str
    proposer_id: str
    reason: str
    operations: list[dict[str, Any]]
    diff: dict[str, Any]
    validation: dict[str, Any]
    request_hash: str
    status: str
    created_at: str


@dataclass(frozen=True, slots=True)
class GraphPatchProposalRecord:
    proposal_id: str
    run_id: str
    user_id: str
    base_revision_id: str
    proposer_type: str
    proposer_id: str
    reason: str
    operations: list[dict[str, Any]]
    diff: dict[str, Any]
    validation: dict[str, Any]
    request_hash: str
    status: str
    candidate_revision: dict[str, Any]
    task_rows: list[dict[str, Any]]
    append_ids: list[str]
    replace_ids: list[str]
    applied_patch_id: str | None
    resolution: str | None
    note: str | None
    resolved_by: str | None
    error: dict[str, Any] | None
    lease_owner: str | None
    lease_version: int
    created_at: str
    resolved_at: str | None
