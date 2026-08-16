"""Persistence records for the durable Agent turn/action protocol."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class RuntimeTurnRecord:
    turn_id: str
    run_id: str
    task_id: str | None
    scope: str
    turn_index: int
    status: str
    model: str | None
    request_hash: str
    response: dict[str, Any] | None
    usage: dict[str, Any]
    stop_reason: str | None
    error: dict[str, Any] | None
    worker_id: str | None
    started_at: str
    finished_at: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ActionIntentRecord:
    action_id: str
    turn_id: str
    run_id: str
    task_id: str | None
    turn_index: int
    action_index: int
    capability_ref: dict[str, Any]
    input: dict[str, Any]
    input_hash: str
    status: str
    side_effect: str
    idempotent: bool
    retryable: bool
    risk: str
    approval_policy: dict[str, Any]
    idempotency_key: str
    invocation_id: str
    worker_id: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ActionObservationRecord:
    observation_id: str
    action_id: str
    run_id: str
    invocation_id: str
    status: str
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    operation: dict[str, Any] | None
    reconciliation_status: str
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
