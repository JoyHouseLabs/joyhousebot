"""Typed owner projection for durable Graph external-event waits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class GraphEventWaitRecord:
    wait_id: str
    run_id: str
    task_id: str
    user_id: str
    event_type: str
    payload_schema: dict[str, Any]
    config_hash: str
    status: str
    token_version: int
    token_issued_at: str | None
    deadline_at: str
    payload: Any
    payload_hash: str | None
    received_at: str | None
    received_by: str | None
    created_at: str
    updated_at: str

    @property
    def token_issued(self) -> bool:
        return self.token_version > 0 and self.token_issued_at is not None
