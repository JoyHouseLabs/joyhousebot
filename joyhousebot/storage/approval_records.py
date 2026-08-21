"""Typed records for durable human approval requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ApprovalRequestRecord:
    approval_id: str
    run_id: str
    action_id: str | None
    task_id: str | None
    subject_type: str
    subject: dict[str, Any]
    user_id: str
    capability_ref: dict[str, Any]
    input_hash: str
    input_preview: dict[str, Any]
    risk: str
    data_classification: str
    required_role: str
    status: str
    requested_by: str
    resolution: str | None
    resolution_note: str | None
    resolved_by: str | None
    consumed_by: str | None
    requested_at: str
    expires_at: str | None
    resolved_at: str | None
    consumed_at: str | None
    updated_at: str
