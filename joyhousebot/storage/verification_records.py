"""Typed persistence records for durable output verification."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class VerificationRecord:
    verification_id: str
    run_id: str
    task_id: str | None
    turn_id: str | None
    user_id: str
    attempt: int
    verifier_id: str
    verifier_type: str
    verifier_version: str
    required: bool
    repairable: bool
    status: str
    policy: dict[str, Any]
    input_hash: str
    evidence: dict[str, Any]
    error: dict[str, Any] | None
    worker_id: str | None
    run_lease_version: int | None
    task_lease_version: int | None
    started_at: str
    finished_at: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
