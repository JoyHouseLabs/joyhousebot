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
    # A capability version is only reproducible when its producing plugin
    # release is pinned as well.  Core capabilities use the explicit
    # ``joyhousebot.core`` release; third-party capabilities use their plugin
    # manifest values.  Plugin registration is allowed to create an unbound
    # definition briefly, then must bind all three values before persistence.
    plugin_id: str = ""
    plugin_version: str = ""
    plugin_build_digest: str = ""

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.capability_id, self.version)):
            raise ValueError("capability id and version are required")
        plugin_values = (
            self.plugin_id,
            self.plugin_version,
            self.plugin_build_digest,
        )
        if any(value.strip() for value in plugin_values) and any(
            not value.strip() for value in plugin_values
        ):
            raise ValueError("plugin provenance must be fully pinned or unbound")

    @property
    def is_bound(self) -> bool:
        return all(
            value.strip()
            for value in (
                self.plugin_id,
                self.plugin_version,
                self.plugin_build_digest,
            )
        )

    def require_bound(self) -> None:
        if not self.is_bound:
            raise ValueError("capability reference must be bound to a plugin release")

    @property
    def identity(self) -> tuple[str, str, str, str, str, str]:
        self.require_bound()
        return (
            self.capability_id,
            self.version,
            self.kind.value,
            self.plugin_id,
            self.plugin_version,
            self.plugin_build_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        self.require_bound()
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "kind": self.kind.value,
            "plugin_id": self.plugin_id,
            "plugin_version": self.plugin_version,
            "plugin_build_digest": self.plugin_build_digest,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CapabilityRef":
        return cls(
            capability_id=str(value["capability_id"]),
            version=str(value["version"]),
            kind=CapabilityKind(str(value["kind"])),
            plugin_id=str(value["plugin_id"]),
            plugin_version=str(value["plugin_version"]),
            plugin_build_digest=str(value["plugin_build_digest"]),
        )


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
    compensation: CapabilityRef | None = None
    # Invocation concurrency is deliberately separate from the durable Task
    # graph's ``execution_mode``.  It describes whether *independent calls
    # returned in a single model response* may overlap.  The Agent/Scenario
    # policy must still opt in before this can take effect.
    invocation_concurrency: str = "parallel_safe"
    max_concurrent_invocations: int = 4
    supports_stream: bool = False
    permissions: tuple[str, ...] = ()
    data_classification: str = "internal"
    connection_ids: tuple[str, ...] = ()
    cost_policy: dict[str, Any] = field(default_factory=dict)
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
        if self.invocation_concurrency not in {"sequential", "parallel_safe"}:
            raise ValueError("invalid capability invocation concurrency")
        if self.max_concurrent_invocations < 1:
            raise ValueError("capability max concurrent invocations must be positive")
        if self.data_classification not in {"public", "internal", "confidential", "restricted"}:
            raise ValueError("invalid capability data classification")
        if any(not item.strip() for item in self.connection_ids):
            raise ValueError("capability connection ids must be non-empty")
        if self.compensation is not None:
            self.compensation.require_bound()
            if self.compensation.kind not in {CapabilityKind.TOOL, CapabilityKind.CONNECTOR}:
                raise ValueError("compensation capability must be a Tool or Connector")
            if self.compensation.identity == self.ref.identity:
                raise ValueError("capability cannot compensate itself")
            if self.side_effect in {"none", "read"}:
                raise ValueError("read-only capability cannot declare compensation")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["ref"] = self.ref.to_dict()
        value["tags"] = list(self.tags)
        value["permissions"] = list(self.permissions)
        value["connection_ids"] = list(self.connection_ids)
        if self.compensation is None:
            value.pop("compensation", None)
        else:
            value["compensation"] = self.compensation.to_dict()
        # Preserve compatibility with already-published built-in capability
        # versions. Plugin provenance is explicit when present, but an empty
        # optional field must not mutate legacy immutable definitions.
        if not value["origin"]:
            value.pop("origin")
        if not value["configuration_schema"]:
            value.pop("configuration_schema")
        # Adding the default contract must not make legacy, immutable
        # definitions appear changed when a newer Worker starts up. Only an
        # explicit non-default declaration is persisted.
        if self.invocation_concurrency == "parallel_safe":
            value.pop("invocation_concurrency", None)
        if self.max_concurrent_invocations == 4:
            value.pop("max_concurrent_invocations", None)
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
        if self.status == InvocationStatus.ACCEPTED and not self.operation:
            raise ValueError("accepted capability result requires an operation descriptor")

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
