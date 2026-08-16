"""Immutable published scenarios and clarification graph contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from porthouse.domain.aggregation import normalize_aggregation_policy
from porthouse.domain.capabilities.models import CapabilityRef
from porthouse.domain.skills import SkillRef


@dataclass(frozen=True, slots=True)
class ScenarioField:
    name: str
    value_type: str
    required: bool = False
    label: str = ""
    description: str = ""
    placeholder: str = ""
    default: Any = None
    enum: tuple[Any, ...] = ()
    # Presentation is deliberately part of the immutable scenario version,
    # rather than a frontend-only hint.  It freezes what a user was asked and
    # lets another channel (web, Slack, WhatsApp, MCP host) render the same
    # request without guessing from a Python type.
    input_mode: str = "auto"
    options: tuple[dict[str, Any], ...] = ()
    allow_other: bool = False
    min_selections: int | None = None
    max_selections: int | None = None
    validation: dict[str, Any] = field(default_factory=dict)
    sensitive: bool = False
    # Business-owned interaction policy. The core persists and exposes these
    # values but never embeds provider- or industry-specific suggestion logic.
    suggestion_provider: dict[str, Any] = field(default_factory=dict)
    normalization: dict[str, Any] = field(default_factory=dict)
    visibility: dict[str, Any] = field(default_factory=dict)
    constraint_policy: dict[str, Any] = field(default_factory=dict)
    confirmation_policy: str = "none"
    examples: tuple[str, ...] = ()
    group: str = ""
    order: int = 0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("scenario field name is required")
        modes = {"auto", "text", "textarea", "single_choice", "multi_choice", "boolean", "number"}
        if self.input_mode not in modes:
            raise ValueError("invalid scenario field input_mode")
        values: list[str] = []
        for option in self.options:
            if not isinstance(option, dict):
                raise ValueError("scenario field options must be objects")
            value = str(option.get("value") or "").strip()
            label = str(option.get("label") or "").strip()
            if not value or not label:
                raise ValueError("scenario field options require value and label")
            values.append(value)
        if len(values) != len(set(values)):
            raise ValueError("scenario field option values must be unique")
        if self.input_mode == "multi_choice" and self.value_type != "array":
            raise ValueError("multi_choice scenario field must use array value_type")
        if self.input_mode == "single_choice" and self.value_type != "string":
            raise ValueError("single_choice scenario field must use string value_type")
        if self.input_mode == "boolean" and self.value_type != "boolean":
            raise ValueError("boolean scenario field must use boolean value_type")
        if self.input_mode == "number" and self.value_type not in {"integer", "number"}:
            raise ValueError("number scenario field must use numeric value_type")
        if self.min_selections is not None and self.min_selections < 0:
            raise ValueError("min_selections cannot be negative")
        if self.max_selections is not None and self.max_selections < 1:
            raise ValueError("max_selections must be positive")
        if (
            self.min_selections is not None
            and self.max_selections is not None
            and self.min_selections > self.max_selections
        ):
            raise ValueError("min_selections cannot exceed max_selections")
        if self.confirmation_policy not in {"none", "inferred", "always", "sensitive"}:
            raise ValueError("invalid scenario field confirmation_policy")
        if any(not str(item).strip() for item in self.examples):
            raise ValueError("scenario field examples must be non-empty")
        strength = self.constraint_policy.get("default_strength")
        if strength is not None and strength not in {"required", "preferred", "excluded"}:
            raise ValueError("invalid default constraint strength")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["enum"] = list(self.enum)
        value["options"] = [dict(item) for item in self.options]
        value["examples"] = list(self.examples)
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
    allowed_capabilities: tuple[CapabilityRef, ...]
    required_skills: tuple[SkillRef, ...] = ()
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
        if any(not isinstance(item, CapabilityRef) for item in self.allowed_capabilities):
            raise ValueError("scenario allowed_capabilities must contain pinned CapabilityRef values")
        if any(item.kind.value not in {"tool", "connector"} for item in self.allowed_capabilities):
            raise ValueError("scenario capabilities must be executable Tools or Connectors")
        identities = [item.identity for item in self.allowed_capabilities]
        if len(identities) != len(set(identities)):
            raise ValueError("scenario allowed capability references must be unique")
        if any(not isinstance(item, SkillRef) for item in self.required_skills):
            raise ValueError("scenario required_skills must contain pinned SkillRef values")
        skill_identities = [item.identity for item in self.required_skills]
        if len(skill_identities) != len(set(skill_identities)):
            raise ValueError("scenario required Skill references must be unique")
        aggregation_policy = self.execution_policy.get("aggregation_policy")
        if aggregation_policy is not None:
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
            from porthouse.domain.scenarios.clarification_conditions import validate_condition

            validate_condition(edge.condition)
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
            "allowed_capabilities": [item.to_dict() for item in self.allowed_capabilities],
            "required_skills": [item.to_dict() for item in self.required_skills],
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
                    label=str(item.get("label") or ""),
                    description=str(item.get("description") or ""),
                    placeholder=str(item.get("placeholder") or ""),
                    default=item.get("default"),
                    enum=tuple(item.get("enum") or ()),
                    input_mode=str(item.get("input_mode") or "auto"),
                    options=tuple(dict(option) for option in item.get("options") or ()),
                    allow_other=bool(item.get("allow_other")),
                    min_selections=(
                        int(item["min_selections"])
                        if item.get("min_selections") is not None
                        else None
                    ),
                    max_selections=(
                        int(item["max_selections"])
                        if item.get("max_selections") is not None
                        else None
                    ),
                    validation=dict(item.get("validation") or {}),
                    sensitive=bool(item.get("sensitive")),
                    suggestion_provider=dict(item.get("suggestion_provider") or {}),
                    normalization=dict(item.get("normalization") or {}),
                    visibility=dict(item.get("visibility") or {}),
                    constraint_policy=dict(item.get("constraint_policy") or {}),
                    confirmation_policy=str(item.get("confirmation_policy") or "none"),
                    examples=tuple(str(example) for example in item.get("examples") or ()),
                    group=str(item.get("group") or ""),
                    order=int(item.get("order") or 0),
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
            allowed_capabilities=tuple(
                CapabilityRef.from_dict(dict(item))
                for item in value.get("allowed_capabilities") or ()
            ),
            required_skills=tuple(
                SkillRef.from_dict(dict(item))
                for item in value.get("required_skills") or ()
            ),
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
        # PostgreSQL stores clarification nodes and edges in normalized tables.
        # Their SQL read order is deliberately canonical rather than insertion
        # order, so immutability comparison must use the same semantic ordering.
        # Field and capability positions remain meaningful and are left intact.
        value["nodes"] = sorted(value["nodes"], key=lambda item: str(item["node_id"]))
        value["edges"] = sorted(
            value["edges"],
            key=lambda item: (
                str(item["source_node_id"]), str(item["target_node_id"]),
                int(item.get("priority") or 0), str(item.get("condition") or ""),
            ),
        )
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
