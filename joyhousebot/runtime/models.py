"""Typed models for the native agent runtime and gateway."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


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
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"


class EventType(str, Enum):
    RUN_ACCEPTED = "run.accepted"
    RUN_QUEUED = "run.queued"
    RUN_CLAIMED = "run.claimed"
    RUN_STARTED = "run.started"
    RUN_PAUSED = "run.paused"
    RUN_WAITING_APPROVAL = "run.waiting_approval"
    RUN_WAITING_EXTERNAL = "run.waiting_external"
    RUN_SCHEDULED = "run.scheduled"
    RUN_RESUMED = "run.resumed"
    RUN_CANCELLING = "run.cancelling"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_TIMED_OUT = "run.timed_out"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    PHASE_STARTED = "phase.started"
    PHASE_PROGRESS = "phase.progress"
    PHASE_COMPLETED = "phase.completed"
    PLAN_CREATED = "plan.created"
    PLAN_UPDATED = "plan.updated"
    PLAN_STEP_STARTED = "plan.step.started"
    PLAN_STEP_COMPLETED = "plan.step.completed"
    PLAN_STEP_FAILED = "plan.step.failed"
    DECISION_RECORDED = "decision.recorded"
    MODEL_REQUEST_STARTED = "model.request.started"
    MODEL_THINKING_STARTED = "model.thinking.started"
    MODEL_THINKING_COMPLETED = "model.thinking.completed"
    MODEL_REASONING_DELTA = "model.reasoning.delta"
    MODEL_RESPONSE_COMPLETED = "model.response.completed"
    MODEL_PROVIDER_FALLBACK = "model.provider_fallback"
    MODEL_RETRY_SCHEDULED = "model.retry_scheduled"
    MODEL_CACHE_HIT = "model.cache.hit"
    CAPABILITY_REQUESTED = "capability.requested"
    CAPABILITY_PERMISSION_REQUESTED = "capability.permission_requested"
    CAPABILITY_PERMISSION_RESOLVED = "capability.permission_resolved"
    CAPABILITY_STARTED = "capability.started"
    CAPABILITY_PROGRESS = "capability.progress"
    CAPABILITY_COMPLETED = "capability.completed"
    CAPABILITY_FAILED = "capability.failed"
    TASK_QUEUED = "task.queued"
    TASK_STARTED = "task.started"
    TASK_PROGRESS = "task.progress"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    TASK_SKIPPED = "task.skipped"
    SUBAGENT_SPAWNED = "subagent.spawned"
    SUBAGENT_CLAIMED = "subagent.claimed"
    SUBAGENT_PROGRESS = "subagent.progress"
    SUBAGENT_COMPLETED = "subagent.completed"
    SUBAGENT_FAILED = "subagent.failed"
    USER_INPUT_REQUESTED = "user_input.requested"
    USER_INPUT_RESOLVED = "user_input.resolved"
    VERIFICATION_STARTED = "verification.started"
    VERIFICATION_PASSED = "verification.passed"
    VERIFICATION_FAILED = "verification.failed"
    ARTIFACT_CREATED = "artifact.created"
    AGGREGATION_STARTED = "aggregation.started"
    AGGREGATION_COMPLETED = "aggregation.completed"
    AGGREGATION_FAILED = "aggregation.failed"
    LEASE_LOST = "lease.lost"
    LEASE_TAKEOVER = "lease.takeover"
    USAGE_UPDATED = "usage.updated"


class EventVisibility(str, Enum):
    """Controls which execution details may leave the runtime boundary."""

    PUBLIC = "public"
    DEBUG = "debug"
    PRIVATE = "private"


@dataclass(slots=True)
class AgentOptions:
    """Per-run options accepted by the native gateway."""

    prompt: str
    user_id: str = "system"
    session_id: str = "main"
    agent_id: str = "default"
    channel: str = "api"
    chat_id: str = "runtime"
    sender_id: str | None = None
    media: list[str] = field(default_factory=list)
    model: str | None = None
    system_prompt: str | None = None
    output_schema: dict[str, Any] | None = None
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
    request_id: str | None = None
    tracker_id: str | None = None
    parent_request_id: str | None = None

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


@dataclass(slots=True)
class AgentEvent:
    run_id: str
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    root_run_id: str | None = None
    parent_run_id: str | None = None
    parent_task_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    turn_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    tool_call_id: str | None = None
    attempt: int | None = None
    phase: str | None = None
    status: str | None = None
    visibility: str = EventVisibility.PUBLIC.value
    summary: str | None = None
    worker_id: str | None = None
    lease_version: int | None = None
    schema_version: int = 2
    sequence: int | None = None
    event_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GraphTaskSpec:
    id: str
    prompt: str
    agent_id: str | None = None
    dependencies: list[str] = field(default_factory=list)
    name: str = ""
    timeout_seconds: float = 300.0
    max_attempts: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    capability_id: str | None = None
    capability_input: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    allowed_tools: list[str] = field(default_factory=list)
    skill_names: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GraphTaskSpec":
        return cls(
            id=str(value.get("id") or uuid4().hex[:12]),
            prompt=str(value.get("prompt") or value.get("description") or ""),
            agent_id=(
                str(value.get("agent_id") or value.get("agentId")).strip() or None
                if value.get("agent_id") or value.get("agentId")
                else None
            ),
            dependencies=[str(item) for item in value.get("dependencies", [])],
            name=str(value.get("name") or ""),
            timeout_seconds=float(
                value.get("timeout_seconds") or value.get("timeoutSeconds") or 300
            ),
            max_attempts=max(1, int(value.get("max_attempts") or value.get("maxAttempts") or 1)),
            metadata=dict(value.get("metadata") or {}),
            capability_id=(str(value["capability_id"]) if value.get("capability_id") else None),
            capability_input=dict(value.get("capability_input") or {}),
            output_schema=(dict(value["output_schema"]) if value.get("output_schema") else None),
            allowed_tools=[str(item) for item in value.get("allowed_tools", [])],
            skill_names=[str(item) for item in value.get("skill_names", [])],
        )


@dataclass(slots=True)
class TaskGraphSpec:
    goal: str
    tasks: list[GraphTaskSpec]
    user_id: str = "system"
    session_id: str = "main"
    agent_id: str = "default"
    max_concurrent: int = 4
    fail_fast: bool = False
    aggregate: bool = True
    # Frozen with the graph at submission time.  See orchestration.aggregation.
    aggregation_policy: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    request_id: str | None = None
    tracker_id: str | None = None
    parent_request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
