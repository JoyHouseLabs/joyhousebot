"""Typed commands accepted by Run application services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from joyhousebot.application.errors import ValidationError
from joyhousebot.domain.capabilities.models import CapabilityRef


@dataclass(slots=True)
class AgentRunTarget:
    mode: str
    agent_id: str
    revision_id: str | None = None


@dataclass(slots=True)
class TeamRunTarget:
    mode: str
    team_id: str
    revision_id: str | None = None


@dataclass(slots=True)
class ScenarioRunTarget:
    mode: str
    scenario_id: str
    version: int
    agent_id: str
    revision_id: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)


RunTarget = AgentRunTarget | TeamRunTarget | ScenarioRunTarget


@dataclass(slots=True)
class CreateRunCommand:
    execution: RunTarget
    session_id: str | None
    input: str
    interaction_mode: str = "auto"
    model: str | None = None
    system_prompt: str | None = None
    allowed_tools: list[str] | None = None
    output_schema: dict[str, Any] | None = None
    verification_policy: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 300.0
    max_turns: int | None = None
    max_repairs: int | None = None
    max_replans: int | None = None
    input_asset_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GraphTaskCommand:
    id: str
    prompt: str
    agent_id: str | None = None
    dependencies: list[str] = field(default_factory=list)
    name: str | None = None
    timeout_seconds: float | None = None
    max_attempts: int = 1
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_cost_usd: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    capability: CapabilityRef | None = None
    capability_input: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    verification_policy: dict[str, Any] = field(default_factory=dict)
    max_repairs: int | None = None
    allowed_tools: list[str] = field(default_factory=list)
    skill_names: list[str] = field(default_factory=list)
    node_type: str | None = None
    branch: dict[str, Any] = field(default_factory=dict)
    foreach: dict[str, Any] = field(default_factory=dict)
    wait_event: dict[str, Any] = field(default_factory=dict)
    approval: dict[str, Any] = field(default_factory=dict)
    verify: dict[str, Any] = field(default_factory=dict)
    compensation: dict[str, Any] = field(default_factory=dict)
    bounded_loop: dict[str, Any] = field(default_factory=dict)
    aggregate: dict[str, Any] = field(default_factory=dict)
    subrun: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.capability, dict):
            self.capability = CapabilityRef.from_dict(self.capability)
        if self.capability is not None and not isinstance(self.capability, CapabilityRef):
            raise ValidationError("graph task capability must be a pinned CapabilityRef")
        resolved_type = self.node_type or ("capability" if self.capability else "agent")
        if resolved_type not in {
            "agent",
            "capability",
            "branch",
            "foreach",
            "wait_event",
            "approval",
            "verify",
            "compensation",
            "bounded_loop",
            "aggregate",
            "subrun",
        }:
            raise ValidationError("unsupported graph node type")
        if resolved_type == "agent" and not self.prompt.strip():
            raise ValidationError("graph task prompt or capability is required")
        if any(
            value is not None and value <= 0
            for value in (self.max_input_tokens, self.max_output_tokens, self.max_cost_usd)
        ):
            raise ValidationError("graph task budgets must be greater than zero")
        if resolved_type in {"capability", "compensation"} and self.capability is None:
            raise ValidationError(f"{resolved_type} graph task requires a pinned CapabilityRef")
        if (
            resolved_type
            in {
                "branch",
                "foreach",
                "wait_event",
                "approval",
                "verify",
                "bounded_loop",
                "aggregate",
                "subrun",
            }
            and self.capability is not None
        ):
            raise ValidationError(f"{resolved_type} graph task cannot directly invoke a capability")
        self.node_type = resolved_type
