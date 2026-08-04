"""Immutable published scenarios and clarification graph contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ScenarioField:
    name: str
    value_type: str
    required: bool = False
    description: str = ""
    default: Any = None
    enum: tuple[Any, ...] = ()
    validation: dict[str, Any] = field(default_factory=dict)
    sensitive: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("scenario field name is required")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["enum"] = list(self.enum)
        return value


@dataclass(frozen=True, slots=True)
class ClarificationNode:
    node_id: str
    kind: str
    question: str
    field_names: tuple[str, ...] = ()
    configuration: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.node_id.strip() or self.kind not in {"question", "confirmation", "terminal"}:
            raise ValueError("invalid clarification node")
        if self.kind != "terminal" and not self.question.strip():
            raise ValueError("non-terminal clarification node requires a question")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["field_names"] = list(self.field_names)
        return value


@dataclass(frozen=True, slots=True)
class ClarificationEdge:
    source_node_id: str
    target_node_id: str
    condition: str = "true"
    priority: int = 100


@dataclass(frozen=True, slots=True)
class ScenarioVersion:
    scenario_id: str
    version: int
    name: str
    description: str
    fields: tuple[ScenarioField, ...]
    nodes: tuple[ClarificationNode, ...]
    edges: tuple[ClarificationEdge, ...]
    allowed_capabilities: tuple[str, ...]
    planning_mode: str = "dynamic"
    execution_policy: dict[str, Any] = field(default_factory=dict)
    routing_rules: tuple[dict[str, Any], ...] = ()
    status: str = "draft"
    published_at: str | None = None

    def __post_init__(self) -> None:
        if not self.scenario_id.strip() or self.version < 1 or not self.name.strip():
            raise ValueError("invalid scenario identity")
        if self.planning_mode not in {"fixed", "dynamic"}:
            raise ValueError("scenario planning mode must be fixed or dynamic")
        if self.status not in {"draft", "published", "retired"}:
            raise ValueError("invalid scenario version status")
        aggregation_policy = self.execution_policy.get("aggregation_policy")
        if aggregation_policy is not None:
            from joyhousebot.orchestration.aggregation import normalize_aggregation_policy

            normalize_aggregation_policy(
                dict(aggregation_policy),
                aggregate=bool(self.execution_policy.get("aggregate", True)),
            )
        self.validate_graph()

    def validate_graph(self) -> None:
        field_names = {item.name for item in self.fields}
        if len(field_names) != len(self.fields):
            raise ValueError("scenario field names must be unique")
        node_ids = {item.node_id for item in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("clarification node ids must be unique")
        for node in self.nodes:
            unknown = set(node.field_names) - field_names
            if unknown:
                raise ValueError(f"clarification node references unknown fields: {sorted(unknown)}")
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
        for edge in self.edges:
            if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
                raise ValueError("clarification edge references an unknown node")
            adjacency[edge.source_node_id].append(edge.target_node_id)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("clarification graph contains a cycle")
            if node_id in visited:
                return
            visiting.add(node_id)
            for child in adjacency[node_id]:
                visit(child)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in node_ids:
            visit(node_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "fields": [item.to_dict() for item in self.fields],
            "nodes": [item.to_dict() for item in self.nodes],
            "edges": [asdict(item) for item in self.edges],
            "allowed_capabilities": list(self.allowed_capabilities),
            "planning_mode": self.planning_mode,
            "execution_policy": self.execution_policy,
            "routing_rules": list(self.routing_rules),
            "status": self.status,
            "published_at": self.published_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ScenarioVersion":
        return cls(
            scenario_id=str(value["scenario_id"]),
            version=int(value["version"]),
            name=str(value["name"]),
            description=str(value.get("description") or ""),
            fields=tuple(
                ScenarioField(
                    name=str(item["name"]),
                    value_type=str(item["value_type"]),
                    required=bool(item.get("required")),
                    description=str(item.get("description") or ""),
                    default=item.get("default"),
                    enum=tuple(item.get("enum") or ()),
                    validation=dict(item.get("validation") or {}),
                    sensitive=bool(item.get("sensitive")),
                )
                for item in value.get("fields") or ()
            ),
            nodes=tuple(
                ClarificationNode(
                    node_id=str(item["node_id"]),
                    kind=str(item["kind"]),
                    question=str(item.get("question") or ""),
                    field_names=tuple(item.get("field_names") or ()),
                    configuration=dict(item.get("configuration") or {}),
                )
                for item in value.get("nodes") or ()
            ),
            edges=tuple(
                ClarificationEdge(
                    source_node_id=str(item["source_node_id"]),
                    target_node_id=str(item["target_node_id"]),
                    condition=str(item.get("condition") or "true"),
                    priority=int(item.get("priority") or 100),
                )
                for item in value.get("edges") or ()
            ),
            allowed_capabilities=tuple(value.get("allowed_capabilities") or ()),
            planning_mode=str(value.get("planning_mode") or "dynamic"),
            execution_policy=dict(value.get("execution_policy") or {}),
            routing_rules=tuple(value.get("routing_rules") or ()),
            status=str(value.get("status") or "draft"),
            published_at=(str(value["published_at"]) if value.get("published_at") else None),
        )

    def definition_dict(self) -> dict[str, Any]:
        """Return immutable business content without lifecycle metadata."""
        value = self.to_dict()
        value.pop("status", None)
        value.pop("published_at", None)
        return value


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    scenario_id: str | None
    scenario_version: int | None
    confidence: float
    execution_class: str
    estimated_duration_seconds: int
    extracted_inputs: dict[str, Any]
    missing_inputs: tuple[str, ...]
    candidate_capabilities: tuple[dict[str, Any], ...]
    next_action: str
    reason_code: str

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("routing confidence must be between 0 and 1")
        if self.execution_class not in {"immediate", "interactive", "background"}:
            raise ValueError("invalid execution class")
        if self.next_action not in {"clarify", "plan", "reject"}:
            raise ValueError("invalid routing next action")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["missing_inputs"] = list(self.missing_inputs)
        value["candidate_capabilities"] = list(self.candidate_capabilities)
        return value
