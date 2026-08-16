"""Durable records for outbound-authenticated local Device Hosts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class DeviceHostRegistrationRecord:
    user_id: str
    device_id: str
    display_name: str
    status: str
    host_revision: str
    host_manifest_digest: str
    public_key_fingerprint: str | None
    is_default: bool
    last_seen_at: str | None
    created_at: str
    updated_at: str
    revoked_at: str | None
    capabilities: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DeviceOperationDeliveryRecord:
    delivery_id: str
    user_id: str
    device_id: str
    reconciliation_id: str
    run_id: str
    task_id: str | None
    action_id: str
    invocation_id: str
    operation_id: str
    capability_ref: dict[str, Any]
    capability_implementation_digest: str
    host_revision: str
    request_digest: str
    request: dict[str, Any]
    portable: bool
    status: str
    attempt_count: int
    max_attempts: int
    delivery_cursor: int
    claim_session_id: str | None
    claim_expires_at: str | None
    claim_version: int
    result: dict[str, Any] | None
    result_digest: str | None
    error: dict[str, Any] | None
    deadline_at: str
    created_at: str
    claimed_at: str | None
    completed_at: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DeviceOperationDeliveryEventRecord:
    delivery_id: str
    event_id: str
    sequence: int
    claim_version: int
    event_type: str
    summary: str
    payload: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
