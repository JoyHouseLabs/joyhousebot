"""Immutable event contracts shared by Runtime and PostgreSQL repositories."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    RUN_HISTORY_PURGED = "run.history_purged"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    PHASE_STARTED = "phase.started"
    PHASE_PROGRESS = "phase.progress"
    PHASE_COMPLETED = "phase.completed"
    PLAN_CREATED = "plan.created"
    PLAN_UPDATED = "plan.updated"
    GRAPH_PATCHED = "graph.patched"
    GRAPH_PATCH_PROPOSED = "graph.patch_proposed"
    GRAPH_PATCH_RESOLVED = "graph.patch_resolved"
    PLAN_STEP_STARTED = "plan.step.started"
    PLAN_STEP_COMPLETED = "plan.step.completed"
    PLAN_STEP_FAILED = "plan.step.failed"
    DECISION_RECORDED = "decision.recorded"
    CONTEXT_BUILT = "context.built"
    TURN_STARTED = "turn.started"
    TURN_RECOVERED = "turn.recovered"
    TURN_COMPLETED = "turn.completed"
    LOOP_STALLED = "loop.stalled"
    LOOP_EXHAUSTED = "loop.exhausted"
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
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    OPERATION_RECONCILIATION_REQUESTED = "operation.reconciliation_requested"
    OPERATION_RECONCILIATION_PROGRESS = "operation.reconciliation_progress"
    OPERATION_RECONCILIATION_RECOVERED = "operation.reconciliation_recovered"
    OPERATION_RECONCILIATION_RESOLVED = "operation.reconciliation_resolved"
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
    BRANCH_EVALUATED = "branch.evaluated"
    FOREACH_EXPANDED = "foreach.expanded"
    FOREACH_COMPLETED = "foreach.completed"
    LOOP_ITERATION_STARTED = "loop.iteration_started"
    LOOP_ITERATION_COMPLETED = "loop.iteration_completed"
    EVENT_WAITING = "event.waiting"
    EVENT_RECEIVED = "event.received"
    EVENT_EXPIRED = "event.expired"
    COMPENSATION_STARTED = "compensation.started"
    COMPENSATION_COMPLETED = "compensation.completed"
    COMPENSATION_FAILED = "compensation.failed"
    SAGA_STARTED = "saga.started"
    SAGA_COMPLETED = "saga.completed"
    SAGA_FAILED = "saga.failed"
    SUBAGENT_SPAWNED = "subagent.spawned"
    SUBAGENT_CLAIMED = "subagent.claimed"
    SUBAGENT_PROGRESS = "subagent.progress"
    SUBAGENT_COMPLETED = "subagent.completed"
    SUBAGENT_FAILED = "subagent.failed"
    SUBRUN_STARTED = "subrun.started"
    SUBRUN_WAITING = "subrun.waiting"
    SUBRUN_COMPLETED = "subrun.completed"
    SUBRUN_FAILED = "subrun.failed"
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
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
