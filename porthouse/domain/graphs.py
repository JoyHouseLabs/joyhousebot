"""Durable task graph specifications shared by planning and execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from porthouse.domain.capabilities.models import CapabilityRef


@dataclass(slots=True)
class GraphTaskSpec:
    id: str
    prompt: str
    agent_id: str | None = None
    dependencies: list[str] = field(default_factory=list)
    name: str = ""
    timeout_seconds: float = 300.0
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
            raise ValueError("graph task capability must be a pinned CapabilityRef")
        if any(
            value is not None and value <= 0
            for value in (self.max_input_tokens, self.max_output_tokens, self.max_cost_usd)
        ):
            raise ValueError("graph task budgets must be greater than zero")
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
            raise ValueError("unsupported graph node type")
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
            raise ValueError(f"{resolved_type} nodes cannot directly invoke a capability")
        if resolved_type in {"capability", "compensation"} and self.capability is None:
            raise ValueError(f"{resolved_type} nodes require a pinned CapabilityRef")
        self.node_type = resolved_type

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
            max_input_tokens=(
                int(value["max_input_tokens"])
                if value.get("max_input_tokens") is not None
                else None
            ),
            max_output_tokens=(
                int(value["max_output_tokens"])
                if value.get("max_output_tokens") is not None
                else None
            ),
            max_cost_usd=(
                float(value["max_cost_usd"])
                if value.get("max_cost_usd") is not None
                else None
            ),
            metadata=dict(value.get("metadata") or {}),
            capability=(
                CapabilityRef.from_dict(dict(value["capability"]))
                if value.get("capability")
                else None
            ),
            capability_input=dict(value.get("capability_input") or {}),
            output_schema=(dict(value["output_schema"]) if value.get("output_schema") else None),
            verification_policy=dict(value.get("verification_policy") or {}),
            max_repairs=(
                int(value["max_repairs"]) if value.get("max_repairs") is not None else None
            ),
            allowed_tools=[str(item) for item in value.get("allowed_tools", [])],
            skill_names=[str(item) for item in value.get("skill_names", [])],
            node_type=(str(value["node_type"]) if value.get("node_type") else None),
            branch=dict(value.get("branch") or {}),
            foreach=dict(value.get("foreach") or {}),
            wait_event=dict(value.get("wait_event") or {}),
            approval=dict(value.get("approval") or {}),
            verify=dict(value.get("verify") or {}),
            compensation=dict(value.get("compensation") or {}),
            bounded_loop=dict(value.get("bounded_loop") or {}),
            aggregate=dict(value.get("aggregate") or {}),
            subrun=dict(value.get("subrun") or {}),
        )


@dataclass(slots=True)
class TaskGraphSpec:
    goal: str
    tasks: list[GraphTaskSpec]
    user_id: str = "system"
    session_id: str = "main"
    agent_id: str = "default"
    # Internal authorities (App/Workflow/Scenario/Team) may freeze the
    # coordinating Agent revision. Public graph requests intentionally leave
    # this unset and resolve the current published revision at submission.
    agent_revision_id: str | None = None
    max_concurrent: int = 4
    fail_fast: bool = False
    failure_policy: dict[str, Any] = field(default_factory=dict)
    aggregate: bool = True
    aggregation_policy: dict[str, Any] = field(default_factory=dict)
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_cost_usd: float | None = None
    idempotency_key: str | None = None
    request_id: str | None = None
    tracker_id: str | None = None
    parent_request_id: str | None = None
    traceparent: str | None = None
    tracestate: str | None = None
    root_run_id: str | None = None
    parent_run_id: str | None = None
    parent_task_id: str | None = None
    max_children_per_root: int | None = None
    input_asset_ids: list[str] = field(default_factory=list)
    # Internal application services may grant a narrow, frozen authority to
    # one purpose-built Graph. Public Graph request schemas do not expose it.
    authority_permissions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if any(
            value is not None and value <= 0
            for value in (self.max_input_tokens, self.max_output_tokens, self.max_cost_usd)
        ):
            raise ValueError("graph budgets must be greater than zero")
        if len(self.input_asset_ids) > 20:
            raise ValueError("a graph may bind at most 20 input assets")
        self.authority_permissions = list(
            dict.fromkeys(
                str(item).strip()
                for item in self.authority_permissions
                if str(item).strip()
            )
        )
