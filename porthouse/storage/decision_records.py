"""Persistence records for structured durable loop decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LoopDecisionRecord:
    decision_id: str
    run_id: str
    task_id: str | None
    scope: str
    decision_index: int
    attempt: int
    decision: str
    reason_code: str
    summary: str
    input_hash: str | None
    output_hash: str | None
    max_replans: int | None
    details: dict[str, Any]
    worker_id: str | None
    run_lease_version: int | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
