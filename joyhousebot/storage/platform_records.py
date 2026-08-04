"""Persistence records for platform capabilities and scenario execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class PlatformAdminRecord:
    """Database-backed platform administrator membership."""

    user_id: str
    role: str
    permissions: tuple[str, ...]
    enabled: bool
    is_test_user: bool
    created_by: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "role": self.role,
            "permissions": list(self.permissions),
            "enabled": self.enabled,
            "is_test_user": self.is_test_user,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class ConfigurationEventRecord:
    sequence: int
    aggregate_type: str
    aggregate_id: str
    revision_id: str
    event_type: str
    actor_id: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ConfigurationRolloutRecord:
    rollout_id: str
    aggregate_type: str
    aggregate_id: str
    revision_id: str
    status: str
    created_by: str
    target_worker_count: int
    acknowledged_worker_count: int
    failed_worker_count: int
    created_at: str
    updated_at: str
    completed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CapabilityInvocationRecord:
    invocation_id: str
    capability_id: str
    capability_version: str
    capability_kind: str
    user_id: str
    agent_id: str
    session_id: str
    run_id: str
    task_id: str | None
    trace_id: str
    status: str
    input: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    idempotency_key: str
    timeout_seconds: int
    attempt: int
    worker_id: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str


@dataclass(slots=True)
class RunScenarioStateRecord:
    run_id: str
    user_id: str
    scenario_id: str
    scenario_version: int
    status: str
    collected_inputs: dict[str, Any]
    missing_inputs: list[str]
    current_node_id: str | None
    routing_decision: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(slots=True)
class InputRequestRecord:
    input_request_id: str
    run_id: str
    user_id: str
    scenario_id: str
    scenario_version: int
    node_id: str
    status: str
    question: str
    fields: list[dict[str, Any]]
    presentation: dict[str, Any]
    source: str
    expires_at: str | None
    created_at: str
    resolved_at: str | None
