"""Persistence records for external capability operation reconciliation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class OperationReconciliationRecord:
    reconciliation_id: str
    run_id: str
    action_id: str
    invocation_id: str
    user_id: str
    capability_ref: dict[str, Any]
    idempotency_key: str
    operation: dict[str, Any]
    status: str
    required_role: str
    attempt_count: int
    max_attempts: int
    next_attempt_at: str | None
    deadline_at: str | None
    lease_owner: str | None
    lease_expires_at: str | None
    lease_version: int
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    last_error: dict[str, Any] | None
    resolution_source: str | None
    resolved_by: str | None
    created_at: str
    updated_at: str
    resolved_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
