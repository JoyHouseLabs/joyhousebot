from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class HostToolRequestRecord:
    request_id: str
    host_request_id: str
    delivery_id: str
    user_id: str
    run_id: str
    task_id: str | None
    agent_id: str
    capability_ref: dict[str, Any]
    input: dict[str, Any]
    input_hash: str
    action_id: str
    turn_id: str
    turn_index: int
    status: str
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    lease_version: int
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HostToolGrantRecord:
    grant_id: str
    delivery_id: str
    user_id: str
    device_id: str
    claim_session_id: str
    claim_version: int
    expires_at: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
