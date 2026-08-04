"""Provider-neutral capability definitions, invocations, and results."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class CapabilityKind(str, Enum):
    TOOL = "tool"
    AGENT = "agent"
    WORKFLOW = "workflow"
    SKILL = "skill"
    CONNECTOR = "connector"


class InvocationStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    ACCEPTED = "accepted"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


_TERMINAL_INVOCATION_STATUSES = {
    InvocationStatus.SUCCEEDED,
    InvocationStatus.FAILED,
    InvocationStatus.CANCELLED,
    InvocationStatus.TIMED_OUT,
}


@dataclass(frozen=True, slots=True)
class CapabilityRef:
    capability_id: str
    version: str
    kind: CapabilityKind

    def __post_init__(self) -> None:
        if not self.capability_id.strip() or not self.version.strip():
            raise ValueError("capability id and version are required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "kind": self.kind.value,
        }


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    ref: CapabilityRef
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    adapter: str
    tags: tuple[str, ...] = ()
    execution_mode: str = "immediate"
    expected_duration_seconds: int = 10
    timeout_seconds: int = 60
    idempotent: bool = True
    retryable: bool = True
    side_effect: str = "none"
    supports_stream: bool = False
    permissions: tuple[str, ...] = ()
    origin: dict[str, str] = field(default_factory=dict)
    # A safe JSON Schema describing operator-owned runtime settings.  It is
    # deliberately distinct from ``configuration``: the latter is immutable
    # version content (for example a Skill's prompt), while settings are a
    # mutable, audited operational overlay.
    configuration_schema: dict[str, Any] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.adapter.strip():
            raise ValueError("capability name and adapter are required")
        if self.input_schema.get("type", "object") != "object":
            raise ValueError("capability input schema must describe an object")
        if self.timeout_seconds <= 0 or self.expected_duration_seconds < 0:
            raise ValueError("capability durations must be positive")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["ref"] = self.ref.to_dict()
        value["tags"] = list(self.tags)
        value["permissions"] = list(self.permissions)
        # Preserve compatibility with already-published built-in capability
        # versions. Plugin provenance is explicit when present, but an empty
        # optional field must not mutate legacy immutable definitions.
        if not value["origin"]:
            value.pop("origin")
        if not value["configuration_schema"]:
            value.pop("configuration_schema")
        return value


@dataclass(frozen=True, slots=True)
class CapabilityError:
    code: str
    message: str
    retryable: bool = False
    retry_after_seconds: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CapabilityMetrics:
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    invocation_id: str
    status: InvocationStatus
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[dict[str, Any], ...] = ()
    operation: dict[str, Any] | None = None
    error: CapabilityError | None = None
    metrics: CapabilityMetrics = field(default_factory=CapabilityMetrics)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.invocation_id.strip():
            raise ValueError("invocation_id is required")
        if self.status == InvocationStatus.FAILED and self.error is None:
            raise ValueError("failed capability result requires an error")
        if self.status != InvocationStatus.FAILED and self.error is not None:
            raise ValueError("only failed capability results may contain an error")

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_INVOCATION_STATUSES

    @property
    def ok(self) -> bool:
        return self.status in {InvocationStatus.SUCCEEDED, InvocationStatus.ACCEPTED}

    @classmethod
    def succeeded(
        cls,
        invocation_id: str,
        *,
        summary: str,
        data: dict[str, Any] | None = None,
        artifacts: tuple[dict[str, Any], ...] = (),
        metrics: CapabilityMetrics | None = None,
    ) -> "CapabilityResult":
        return cls(
            invocation_id=invocation_id,
            status=InvocationStatus.SUCCEEDED,
            summary=summary,
            data=data or {},
            artifacts=artifacts,
            metrics=metrics or CapabilityMetrics(),
        )

    @classmethod
    def failed(
        cls,
        invocation_id: str,
        *,
        code: str,
        message: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        metrics: CapabilityMetrics | None = None,
    ) -> "CapabilityResult":
        return cls(
            invocation_id=invocation_id,
            status=InvocationStatus.FAILED,
            summary=message,
            error=CapabilityError(code, message, retryable, details=details or {}),
            metrics=metrics or CapabilityMetrics(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "invocation_id": self.invocation_id,
            "status": self.status.value,
            "summary": self.summary,
            "data": self.data,
            "artifacts": list(self.artifacts),
            "operation": self.operation,
            "error": self.error.to_dict() if self.error else None,
            "metrics": self.metrics.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CapabilityInvocation:
    capability: CapabilityRef
    user_id: str
    agent_id: str
    session_id: str
    run_id: str
    task_id: str | None
    trace_id: str
    input: dict[str, Any]
    timeout_seconds: int
    idempotency_key: str
    invocation_id: str = field(default_factory=lambda: f"inv_{uuid4().hex}")
    permission_mode: str = "default"

    def __post_init__(self) -> None:
        required = (
            self.user_id,
            self.agent_id,
            self.session_id,
            self.run_id,
            self.trace_id,
            self.idempotency_key,
        )
        if any(not value.strip() for value in required):
            raise ValueError("capability invocation identity is incomplete")
        if self.timeout_seconds <= 0:
            raise ValueError("capability invocation timeout must be positive")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["capability"] = self.capability.to_dict()
        return value
