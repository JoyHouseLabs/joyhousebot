"""Capability execution contracts.

Business implementations receive an opaque context and return structured
results.  They never need to import the framework storage or HTTP layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from joyhousebot.contracts.artifacts import Artifact


@dataclass(slots=True)
class CapabilityContext:
    user_id: str
    session_id: str
    run_id: str
    task_id: str | None = None
    agent_id: str | None = None
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CapabilityResult:
    success: bool
    output: Any = None
    error: dict[str, Any] | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    status: Literal["succeeded", "accepted"] = "succeeded"
    operation: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status == "accepted" and not self.operation:
            raise ValueError("accepted capability result requires an operation descriptor")


@dataclass(slots=True)
class OperationReconciliationResult:
    """Provider-neutral result of querying an already-started operation."""

    status: Literal["pending", "succeeded", "failed", "unknown"]
    summary: str = ""
    output: Any = None
    error: dict[str, Any] | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    operation: dict[str, Any] | None = None
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.status == "failed" and self.error is None:
            raise ValueError("failed reconciliation requires an error")
        if self.status != "failed" and self.error is not None:
            raise ValueError("only failed reconciliation may contain an error")


@runtime_checkable
class CapabilityHandler(Protocol):
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        """Execute one capability invocation."""


@runtime_checkable
class OperationReconciler(Protocol):
    async def reconcile_operation(
        self, context: CapabilityContext, operation: dict[str, Any]
    ) -> OperationReconciliationResult:
        """Query an operation by its frozen provider/idempotency identity."""
