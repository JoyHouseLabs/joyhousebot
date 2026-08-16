"""Records for Host model grants and transactional budget reservations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class HostModelGrantRecord:
    grant_id: str
    user_id: str
    run_id: str
    task_id: str | None
    action_id: str
    delivery_id: str
    device_id: str
    capability_ref: dict[str, Any]
    provider_id: str
    provider_revision_id: str
    model_id: str
    token_budget: int
    cost_budget_micros: int
    reserved_tokens: int
    used_tokens: int
    reserved_cost_micros: int
    used_cost_micros: int
    active_reservations: int
    max_concurrent: int
    status: str
    expires_at: str
    created_at: str
    updated_at: str
    revoked_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HostModelReservationRecord:
    reservation_id: str
    grant_id: str
    request_id: str
    reserved_tokens: int
    reserved_cost_micros: int
    actual_tokens: int | None
    actual_cost_micros: int | None
    status: str
    usage: dict[str, Any]
    response: dict[str, Any] | None
    created_at: str
    expires_at: str
    settled_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
