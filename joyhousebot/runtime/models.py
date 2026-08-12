"""Typed request and result models for the native agent runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from joyhousebot.contracts.events import AgentEvent, EventType, EventVisibility
from joyhousebot.domain.graphs import GraphTaskSpec, TaskGraphSpec

__all__ = [
    "AgentEvent",
    "AgentOptions",
    "AgentResult",
    "AgentUsage",
    "EventType",
    "EventVisibility",
    "GraphTaskSpec",
    "RunStatus",
    "TaskGraphSpec",
    "TaskStatus",
    "utc_now",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_EXTERNAL = "waiting_external"
    SCHEDULED = "scheduled"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    BLOCKED = "blocked"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_EXTERNAL = "waiting_external"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"
    DORMANT = "dormant"


@dataclass(slots=True)
class AgentOptions:
    """Per-run options accepted by the native gateway."""

    prompt: str
    user_id: str = "system"
    session_id: str = "main"
    agent_id: str = "default"
    # Control-plane Eval runs may pin an unpublished candidate revision.
    # Public run schemas never expose this field.
    agent_revision_id: str | None = None
    channel: str = "api"
    chat_id: str = "runtime"
    sender_id: str | None = None
    media: list[str] = field(default_factory=list)
    model: str | None = None
    system_prompt: str | None = None
    output_schema: dict[str, Any] | None = None
    verification_policy: dict[str, Any] = field(default_factory=dict)
    max_repairs: int | None = None
    max_replans: int | None = None
    timeout_seconds: float = 300.0
    max_turns: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_cost_usd: float | None = None
    permission_mode: str = "default"
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    root_run_id: str | None = None
    parent_run_id: str | None = None
    parent_task_id: str | None = None
    max_children_per_root: int | None = None
    input_asset_ids: list[str] = field(default_factory=list)
    request_id: str | None = None
    tracker_id: str | None = None
    parent_request_id: str | None = None
    traceparent: str | None = None
    tracestate: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentOptions":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: item for key, item in value.items() if key in allowed})


@dataclass(slots=True)
class AgentUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AgentResult:
    run_id: str
    status: RunStatus
    content: str | None = None
    structured_output: Any = None
    stop_reason: str | None = None
    error: str | None = None
    usage: AgentUsage = field(default_factory=AgentUsage)
    tools_used: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value
