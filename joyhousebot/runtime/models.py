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
    billed_input_tokens: int = 0
    billed_output_tokens: int = 0
    billed_total_tokens: int = 0
    cost_usd: float | None = None
    model_invocations: int = 0
    missing_usage_invocations: int = 0
    partial_usage_invocations: int = 0
    missing_billing_invocations: int = 0
    usage_status: str = "exact"
    billing_status: str = "exact"
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "AgentUsage":
        source = dict(value or {})
        input_tokens = int(source.get("input_tokens", source.get("prompt_tokens", 0)) or 0)
        output_tokens = int(
            source.get("output_tokens", source.get("completion_tokens", 0)) or 0
        )
        cache_hit = source.get("usage_source") == "cache"
        billed_input = int(
            source.get(
                "billed_input_tokens",
                0 if cache_hit else input_tokens,
            )
            or 0
        )
        billed_output = int(
            source.get(
                "billed_output_tokens",
                0 if cache_hit else output_tokens,
            )
            or 0
        )
        has_usage = any(
            key in source
            for key in ("input_tokens", "output_tokens", "prompt_tokens", "completion_tokens")
        )
        model_invocations = (
            int(source["model_invocations"])
            if "model_invocations" in source
            else (1 if has_usage else 0)
        )
        usage_status = str(source.get("usage_status") or ("exact" if has_usage else "missing"))
        raw_cost = next(
            (
                source[key]
                for key in ("cost_usd", "total_cost", "cost")
                if source.get(key) is not None
            ),
            None,
        )
        has_cost = raw_cost is not None
        billing_status = str(
            source.get("billing_status")
            or ("not_billed" if cache_hit else "exact" if has_cost else "missing")
        )
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=int(source.get("total_tokens") or input_tokens + output_tokens),
            billed_input_tokens=billed_input,
            billed_output_tokens=billed_output,
            billed_total_tokens=int(
                source.get("billed_total_tokens") or billed_input + billed_output
            ),
            cost_usd=(float(raw_cost) if has_cost else None),
            model_invocations=model_invocations,
            missing_usage_invocations=int(
                source["missing_usage_invocations"]
                if "missing_usage_invocations" in source
                else (model_invocations if usage_status == "missing" else 0)
            ),
            partial_usage_invocations=int(
                source["partial_usage_invocations"]
                if "partial_usage_invocations" in source
                else (model_invocations if usage_status == "partial" else 0)
            ),
            missing_billing_invocations=int(
                source["missing_billing_invocations"]
                if "missing_billing_invocations" in source
                else (model_invocations if billing_status == "missing" else 0)
            ),
            usage_status=usage_status,
            billing_status=billing_status,
            model=str(source.get("model") or "") or None,
        )

    def add(self, other: "AgentUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens = self.input_tokens + self.output_tokens
        self.billed_input_tokens += other.billed_input_tokens
        self.billed_output_tokens += other.billed_output_tokens
        self.billed_total_tokens = self.billed_input_tokens + self.billed_output_tokens
        if other.cost_usd is not None:
            self.cost_usd = float(self.cost_usd or 0) + other.cost_usd
        self.model_invocations += other.model_invocations
        self.missing_usage_invocations += other.missing_usage_invocations
        self.partial_usage_invocations += other.partial_usage_invocations
        self.missing_billing_invocations += other.missing_billing_invocations
        if other.model:
            self.model = other.model
        self._refresh_statuses()

    def _refresh_statuses(self) -> None:
        if self.model_invocations and self.missing_usage_invocations >= self.model_invocations:
            self.usage_status = "missing"
        elif self.missing_usage_invocations or self.partial_usage_invocations:
            self.usage_status = "partial"
        else:
            self.usage_status = "exact"
        if self.model_invocations and self.missing_billing_invocations >= self.model_invocations:
            self.billing_status = "missing"
        elif self.missing_billing_invocations:
            self.billing_status = "partial"
        else:
            self.billing_status = "exact"


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
