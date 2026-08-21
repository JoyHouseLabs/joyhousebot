"""Durable task graph specifications shared by planning and execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from joyhousebot.domain.capabilities.models import CapabilityRef


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "agent_id": self.agent_id,
            "dependencies": list(self.dependencies),
            "name": self.name,
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_cost_usd": self.max_cost_usd,
            "metadata": dict(self.metadata),
            "capability": self.capability.to_dict() if self.capability else None,
            "capability_input": dict(self.capability_input),
            "output_schema": dict(self.output_schema) if self.output_schema else None,
            "verification_policy": dict(self.verification_policy),
            "max_repairs": self.max_repairs,
            "allowed_tools": list(self.allowed_tools),
            "skill_names": list(self.skill_names),
            "node_type": self.node_type,
            "branch": dict(self.branch),
            "foreach": dict(self.foreach),
            "wait_event": dict(self.wait_event),
            "approval": dict(self.approval),
            "verify": dict(self.verify),
            "compensation": dict(self.compensation),
            "bounded_loop": dict(self.bounded_loop),
            "aggregate": dict(self.aggregate),
            "subrun": dict(self.subrun),
        }


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

    def to_dict(self) -> dict[str, Any]:
        """Serialize the frozen spec for plan-preview artifacts.

        Round-trips through :meth:`from_dict`; a confirmed plan materializes
        exactly this spec so the preview is the execution.
        """
        return {
            "goal": self.goal,
            "tasks": [task.to_dict() for task in self.tasks],
            "user_id": self.user_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "agent_revision_id": self.agent_revision_id,
            "max_concurrent": self.max_concurrent,
            "fail_fast": self.fail_fast,
            "failure_policy": dict(self.failure_policy),
            "aggregate": self.aggregate,
            "aggregation_policy": dict(self.aggregation_policy),
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_cost_usd": self.max_cost_usd,
            "idempotency_key": self.idempotency_key,
            "request_id": self.request_id,
            "tracker_id": self.tracker_id,
            "parent_request_id": self.parent_request_id,
            "traceparent": self.traceparent,
            "tracestate": self.tracestate,
            "root_run_id": self.root_run_id,
            "parent_run_id": self.parent_run_id,
            "parent_task_id": self.parent_task_id,
            "max_children_per_root": self.max_children_per_root,
            "input_asset_ids": list(self.input_asset_ids),
            "authority_permissions": list(self.authority_permissions),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TaskGraphSpec":
        def optional_int(key: str) -> int | None:
            return int(value[key]) if value.get(key) is not None else None

        def optional_float(key: str) -> float | None:
            return float(value[key]) if value.get(key) is not None else None

        def optional_str(key: str) -> str | None:
            return str(value[key]) if value.get(key) is not None else None

        return cls(
            goal=str(value.get("goal") or ""),
            tasks=[GraphTaskSpec.from_dict(dict(item)) for item in value.get("tasks") or ()],
            user_id=str(value.get("user_id") or "system"),
            session_id=str(value.get("session_id") or "main"),
            agent_id=str(value.get("agent_id") or "default"),
            agent_revision_id=optional_str("agent_revision_id"),
            max_concurrent=int(value.get("max_concurrent") or 4),
            fail_fast=bool(value.get("fail_fast")),
            failure_policy=dict(value.get("failure_policy") or {}),
            aggregate=bool(value.get("aggregate")),
            aggregation_policy=dict(value.get("aggregation_policy") or {}),
            max_input_tokens=optional_int("max_input_tokens"),
            max_output_tokens=optional_int("max_output_tokens"),
            max_cost_usd=optional_float("max_cost_usd"),
            idempotency_key=optional_str("idempotency_key"),
            request_id=optional_str("request_id"),
            tracker_id=optional_str("tracker_id"),
            parent_request_id=optional_str("parent_request_id"),
            traceparent=optional_str("traceparent"),
            tracestate=optional_str("tracestate"),
            root_run_id=optional_str("root_run_id"),
            parent_run_id=optional_str("parent_run_id"),
            parent_task_id=optional_str("parent_task_id"),
            max_children_per_root=optional_int("max_children_per_root"),
            input_asset_ids=[str(item) for item in value.get("input_asset_ids") or ()],
            authority_permissions=[str(item) for item in value.get("authority_permissions") or ()],
            metadata=dict(value.get("metadata") or {}),
        )
