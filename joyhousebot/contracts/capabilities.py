"""Capability execution contracts.

Business implementations receive an opaque context and return structured
results.  They never need to import the framework storage or HTTP layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

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


@runtime_checkable
class CapabilityHandler(Protocol):
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        """Execute one capability invocation."""
