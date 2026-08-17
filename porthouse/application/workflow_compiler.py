"""Validate and compile Workflow documents into durable Graph task specs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from porthouse.application.errors import ValidationError
from porthouse.application.workflow_capabilities import (
    capability_task_spec,
    normalize_explicit_aggregation,
    resolve_capability_reference,
)
from porthouse.application.workflow_revisions import freeze_workflow_agent_revision
from porthouse.orchestration.task_graph import validate_and_order_graph
from porthouse.runtime.models import GraphTaskSpec
from porthouse.storage.contracts import RuntimeStores

_NODE_KINDS = frozenset(
    {
        "agent",
        "team",
        "scenario",
        "approval",
        "verify",
        "branch",
        "bounded_loop",
        "capability",
    }
)


@dataclass(frozen=True, slots=True)
class WorkflowCatalog:
    agents: frozenset[str]
    tools: frozenset[str]
    skills: dict[str, dict[str, str]]
    public: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class WorkflowNodeContext:
    catalog: WorkflowCatalog
    coordinator_agent_id: str


def required_text(value: Any, *, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValidationError(f"{field} is required")
    if len(normalized) > maximum:
        raise ValidationError(f"{field} must be at most {maximum} characters")
    return normalized


class WorkflowCompiler:
    """Freeze published references and normalize one Workflow graph document."""

    def __init__(self, stores: RuntimeStores, *, default_agent_id: str) -> None:
        self.stores = stores
        self.default_agent_id = default_agent_id

    def catalog(self) -> WorkflowCatalog:
        profiles = self.stores.catalog.list_agent_profiles()
        agents = frozenset(item.definition.agent_id for item in profiles)
        capabilities = self.stores.catalog.list_capability_definitions()
        tools = frozenset(
            str(item.get("ref", {}).get("capability_id"))
            for item in capabilities
            if item.get("ref", {}).get("kind") in {"tool", "connector"}
        )
        skill_definitions = self.stores.catalog.list_skills(active_only=True)
        skills = {
            str(item["skill_id"]).removeprefix("skill."): {
                "skill_id": str(item["skill_id"]),
                "version": str(dict(item.get("current") or {}).get("version") or ""),
                "content_sha256": str(
                    dict(item.get("current") or {}).get("content_sha256") or ""
                ),
            }
            for item in skill_definitions
            if item.get("current")
        }
        public = [
            {
                "id": str(item.get("ref", {}).get("capability_id")),
                "kind": str(item.get("ref", {}).get("kind")),
                "name": str(item.get("name") or ""),
                "description": str(item.get("description") or "")[:500],
            }
            for item in capabilities
            if item.get("ref", {}).get("kind") in {"tool", "connector"}
        ]
        public.extend(
            {
                "id": str(item["skill_id"]),
                "kind": "skill",
                "name": str(item.get("name") or ""),
                "description": str(item.get("description") or "")[:500],
                "version": str(dict(item.get("current") or {}).get("version") or ""),
            }
            for item in skill_definitions
            if item.get("current")
        )
        return WorkflowCatalog(agents, tools, skills, tuple(public))

    def normalize(self, value: dict[str, Any]) -> dict[str, Any]:
        catalog = self.catalog()
        coordinator_id, coordinator_revision_id = self._coordinator(value, catalog)
        source_nodes = value.get("nodes")
        if not isinstance(source_nodes, list) or not 1 <= len(source_nodes) <= 32:
            raise ValidationError("Workflow requires between 1 and 32 nodes")
        context = WorkflowNodeContext(catalog, coordinator_id)
        nodes = [
            self._normalize_node(raw, index=index, context=context)
            for index, raw in enumerate(source_nodes)
        ]
        graph = self._assemble_graph(
            value,
            nodes,
            coordinator_id=coordinator_id,
            coordinator_revision_id=coordinator_revision_id,
        )
        try:
            validate_and_order_graph(
                compile_workflow_tasks(graph, goal=graph["summary"] or graph["name"])
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return graph

    def _coordinator(
        self, value: dict[str, Any], catalog: WorkflowCatalog
    ) -> tuple[str, str]:
        requested = str(
            value.get("coordinator_agent_id") or self.default_agent_id
        ).strip()
        agent_id = requested if requested in catalog.agents else self.default_agent_id
        revision_id = freeze_workflow_agent_revision(
            self.stores.catalog,
            agent_id,
            str(value.get("coordinator_agent_revision_id") or "").strip() or None,
            field="Workflow coordinator",
        )
        return agent_id, revision_id

    def _normalize_node(
        self,
        raw: Any,
        *,
        index: int,
        context: WorkflowNodeContext,
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValidationError("Workflow nodes must be objects")
        node_id = str(raw.get("id") or f"step-{index + 1}").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", node_id):
            raise ValidationError(f"invalid Workflow node id: {node_id}")
        kind = str(raw.get("kind") or "agent")
        if kind not in _NODE_KINDS:
            raise ValidationError(f"unsupported Workflow node kind: {kind}")
        raw_objective = str(raw.get("objective") or raw.get("prompt") or "").strip()
        objective = (
            raw_objective[:2000]
            if kind == "capability"
            else required_text(
                raw_objective, field=f"node {node_id} objective", maximum=2000
            )
        )
        configuration = dict(raw.get("configuration") or {})
        agent_id, revision_id, subrun = self._freeze_execution_binding(
            raw,
            node_id=node_id,
            kind=kind,
            configuration=configuration,
            context=context,
        )
        dependencies = list(
            dict.fromkeys(str(item) for item in raw.get("dependencies") or [])
        )
        tools, skills = self._allowed_agent_capabilities(raw, context.catalog)
        capability, capability_input = self._capability_binding(
            raw, node_id=node_id, kind=kind, configuration=configuration
        )
        return {
            "id": node_id,
            "name": required_text(
                raw.get("name") or node_id,
                field=f"node {node_id} name",
                maximum=128,
            ),
            "objective": objective,
            "kind": kind,
            "agent_id": agent_id if kind in {"agent", "team", "scenario"} else None,
            "agent_revision_id": revision_id,
            "dependencies": dependencies,
            "allowed_tools": tools if kind == "agent" else [],
            "skills": skills if kind == "agent" else [],
            "skill_refs": (
                [context.catalog.skills[name] for name in skills]
                if kind == "agent"
                else []
            ),
            "max_attempts": _max_attempts(raw, kind),
            "configuration": configuration,
            "subrun": subrun,
            "capability": capability,
            "capability_input": capability_input,
            "output_schema": (
                dict(raw["output_schema"])
                if isinstance(raw.get("output_schema"), dict)
                else None
            ),
            "verification_policy": dict(raw.get("verification_policy") or {}),
        }

    def _freeze_execution_binding(
        self,
        raw: dict[str, Any],
        *,
        node_id: str,
        kind: str,
        configuration: dict[str, Any],
        context: WorkflowNodeContext,
    ) -> tuple[str, str | None, dict[str, Any]]:
        requested = str(raw.get("agent_id") or "").strip()
        agent_id = (
            requested if requested in context.catalog.agents else self.default_agent_id
        )
        if kind == "team":
            return self._freeze_team(raw, node_id=node_id, configuration=configuration)
        if kind == "scenario":
            return self._freeze_scenario(
                raw,
                node_id=node_id,
                agent_id=agent_id,
                configuration=configuration,
            )
        if kind == "agent":
            revision_id = freeze_workflow_agent_revision(
                self.stores.catalog,
                agent_id,
                str(raw.get("agent_revision_id") or "").strip() or None,
                field=f"Workflow node {node_id}",
            )
            return agent_id, revision_id, {}
        if kind == "bounded_loop":
            self._freeze_loop_template(
                configuration, node_id=node_id, context=context
            )
        return agent_id, None, {}

    def _freeze_team(
        self,
        raw: dict[str, Any],
        *,
        node_id: str,
        configuration: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        frozen = dict(raw.get("subrun") or {})
        team = (
            self.stores.catalog.get_agent_team_revision(
                str(frozen.get("team_revision_id") or "")
            )
            if frozen.get("team_revision_id")
            else self.stores.catalog.get_published_agent_team(
                str(raw.get("team_id") or configuration.get("team_id") or "")
            )
        )
        if team is None or team.status not in {"published", "retired"}:
            raise ValidationError(f"Workflow Team node {node_id} is not published")
        subrun = {
            "mode": "team",
            "team_id": team.team_id,
            "team_revision_id": team.revision_id,
            "team_version": team.version,
            "coordinator_member_id": team.coordinator_member_id,
            "coordinator_agent_id": team.coordinator.agent_id,
            "coordinator_agent_revision_id": team.coordinator.agent_revision_id,
            "max_children_per_root": _max_children(configuration),
        }
        return team.coordinator.agent_id, team.coordinator.agent_revision_id, subrun

    def _freeze_scenario(
        self,
        raw: dict[str, Any],
        *,
        node_id: str,
        agent_id: str,
        configuration: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        frozen = dict(raw.get("subrun") or {})
        scenario_id = str(
            frozen.get("scenario_id")
            or raw.get("scenario_id")
            or configuration.get("scenario_id")
            or ""
        )
        version = (
            frozen.get("scenario_version")
            or raw.get("scenario_version")
            or configuration.get("scenario_version")
        )
        scenario = self.stores.scenarios.get_scenario_version(
            scenario_id, int(version) if version is not None else None
        )
        if (
            scenario is None
            or scenario.status not in {"published", "retired"}
            or scenario.planning_mode != "fixed"
        ):
            raise ValidationError(
                f"Workflow Scenario node {node_id} requires a published fixed revision"
            )
        inputs = dict(
            frozen.get("inputs")
            or raw.get("scenario_inputs")
            or configuration.get("inputs")
            or {}
        )
        missing = [
            item.name
            for item in scenario.fields
            if item.required and item.name not in inputs
        ]
        if missing:
            raise ValidationError(
                f"Workflow Scenario node {node_id} has unresolved inputs: {missing}"
            )
        revision_id = freeze_workflow_agent_revision(
            self.stores.catalog,
            agent_id,
            str(
                frozen.get("agent_revision_id") or raw.get("agent_revision_id") or ""
            ).strip()
            or None,
            field=f"Workflow Scenario node {node_id}",
        )
        return agent_id, revision_id, {
            "mode": "scenario",
            "scenario_id": scenario.scenario_id,
            "scenario_version": scenario.version,
            "planning_mode": scenario.planning_mode,
            "agent_id": agent_id,
            "agent_revision_id": revision_id,
            "inputs": inputs,
            "max_children_per_root": _max_children(configuration),
        }

    def _freeze_loop_template(
        self,
        configuration: dict[str, Any],
        *,
        node_id: str,
        context: WorkflowNodeContext,
    ) -> None:
        template = dict(configuration.get("template") or {})
        template_type = str(
            template.get("node_type")
            or ("capability" if template.get("capability") else "agent")
        )
        if template_type != "agent":
            return
        agent_id = str(
            template.get("agent_id") or context.coordinator_agent_id
        ).strip()
        if agent_id not in context.catalog.agents:
            raise ValidationError(
                f"Workflow bounded_loop node {node_id} Agent is not published"
            )
        metadata = dict(template.get("metadata") or {})
        metadata["agent_revision_id"] = freeze_workflow_agent_revision(
            self.stores.catalog,
            agent_id,
            str(metadata.get("agent_revision_id") or "").strip() or None,
            field=f"Workflow bounded_loop node {node_id}",
        )
        template.update({"agent_id": agent_id, "metadata": metadata})
        configuration["template"] = template

    @staticmethod
    def _allowed_agent_capabilities(
        raw: dict[str, Any], catalog: WorkflowCatalog
    ) -> tuple[list[str], list[str]]:
        tools = [
            item
            for item in dict.fromkeys(
                str(item) for item in raw.get("allowed_tools") or []
            )
            if item in catalog.tools
        ]
        skills = [
            item.removeprefix("skill.")
            for item in dict.fromkeys(str(item) for item in raw.get("skills") or [])
            if item.removeprefix("skill.") in catalog.skills
        ]
        return tools, skills

    def _capability_binding(
        self,
        raw: dict[str, Any],
        *,
        node_id: str,
        kind: str,
        configuration: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if kind != "capability":
            return None, {}
        raw_capability = (
            raw.get("capability")
            if raw.get("capability") is not None
            else configuration.get("capability")
        )
        reference = resolve_capability_reference(
            self.stores.catalog,
            raw_capability,
            node_id=node_id,
        )
        inputs = dict(raw.get("capability_input") or configuration.get("input") or {})
        return reference, inputs

    @staticmethod
    def _assemble_graph(
        value: dict[str, Any],
        nodes: list[dict[str, Any]],
        *,
        coordinator_id: str,
        coordinator_revision_id: str,
    ) -> dict[str, Any]:
        policies = dict(value.get("policies") or {})
        aggregation = normalize_explicit_aggregation(policies.get("aggregation"))
        return {
            "schema_version": 1,
            "coordinator_agent_id": coordinator_id,
            "coordinator_agent_revision_id": coordinator_revision_id,
            "name": required_text(
                value.get("name") or "智能工作流", field="name", maximum=128
            ),
            "summary": str(value.get("summary") or "").strip()[:1000],
            "risk_level": (
                str(value.get("risk_level"))
                if str(value.get("risk_level")) in {"low", "medium", "high"}
                else "medium"
            ),
            "estimated_duration_minutes": max(
                0, min(int(value.get("estimated_duration_minutes") or 0), 10080)
            ),
            "nodes": nodes,
            "edges": [
                {"source": dependency, "target": node["id"]}
                for node in nodes
                for dependency in node["dependencies"]
            ],
            "policies": {
                "max_concurrent": max(
                    1, min(int(policies.get("max_concurrent") or 4), 16)
                ),
                "fail_fast": bool(policies.get("fail_fast", True)),
                "aggregate": bool(policies.get("aggregate", True)),
                **({"aggregation": aggregation} if aggregation else {}),
            },
        }


def compile_workflow_tasks(
    graph: dict[str, Any], *, goal: str
) -> list[GraphTaskSpec]:
    return [_compile_workflow_task(node, graph=graph, goal=goal) for node in graph["nodes"]]


def _compile_workflow_task(
    node: dict[str, Any], *, graph: dict[str, Any], goal: str
) -> GraphTaskSpec:
    kind = node["kind"]
    if kind == "approval":
        approval = {
            "title": node["name"],
            "description": node["objective"],
            "required_role": "owner",
            "risk": graph["risk_level"],
            "data_classification": "internal",
            "expires_in_seconds": 86_400,
            **dict(node.get("configuration") or {}),
        }
        return GraphTaskSpec(
            id=node["id"],
            name=node["name"],
            prompt=node["objective"],
            dependencies=list(node["dependencies"]),
            node_type="approval",
            max_attempts=1,
            approval=approval,
        )
    prompt = (
        f"Overall Workflow goal:\n{goal}\n\nAssigned objective:\n{node['objective']}\n\n"
        "Complete only this node and return a concise, verifiable result for downstream nodes."
    )
    if kind in {"team", "scenario"}:
        return GraphTaskSpec(
            id=node["id"],
            name=node["name"],
            agent_id=node["agent_id"],
            prompt=prompt,
            dependencies=list(node["dependencies"]),
            node_type="subrun",
            max_attempts=1,
            subrun=dict(node["subrun"]),
            metadata={"workflow_node": node["id"]},
        )
    if kind in {"verify", "branch", "bounded_loop"}:
        configuration = dict(node.get("configuration") or {})
        return GraphTaskSpec(
            id=node["id"],
            name=node["name"],
            prompt=node["objective"],
            dependencies=list(node["dependencies"]),
            max_attempts=1,
            node_type=kind,
            verify=configuration if kind == "verify" else {},
            branch=configuration if kind == "branch" else {},
            bounded_loop=configuration if kind == "bounded_loop" else {},
            output_schema=node.get("output_schema"),
            verification_policy=dict(node.get("verification_policy") or {}),
            metadata={"workflow_node": node["id"]},
        )
    if kind == "capability":
        return capability_task_spec(node)
    return GraphTaskSpec(
        id=node["id"],
        name=node["name"],
        agent_id=node["agent_id"],
        prompt=prompt,
        dependencies=list(node["dependencies"]),
        max_attempts=node["max_attempts"],
        allowed_tools=list(node["allowed_tools"]),
        skill_names=list(node["skills"]),
        output_schema=node.get("output_schema"),
        verification_policy=dict(node.get("verification_policy") or {}),
        metadata={
            "workflow_node": node["id"],
            "agent_revision_id": node["agent_revision_id"],
            "skill_refs": list(node.get("skill_refs") or []),
        },
    )


def _max_attempts(raw: dict[str, Any], kind: str) -> int:
    if kind not in {"agent", "capability"}:
        return 1
    default = 3 if kind == "capability" else 1
    return max(1, min(int(raw.get("max_attempts") or default), 5))


def _max_children(configuration: dict[str, Any]) -> int:
    return min(256, max(1, int(configuration.get("max_children_per_root") or 32)))


__all__ = [
    "WorkflowCompiler",
    "compile_workflow_tasks",
    "required_text",
]
