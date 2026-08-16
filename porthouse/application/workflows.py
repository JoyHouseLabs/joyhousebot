"""Natural-language Workflow design, versioning, publication and execution."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from uuid import uuid4

from porthouse.application.context import RequestContext
from porthouse.application.errors import ConflictError, NotFoundError, ValidationError
from porthouse.application.workflow_contracts import (
    WORKFLOW_CONTROL_GUIDE,
    WORKFLOW_DESIGN_SCHEMA,
)
from porthouse.application.workflow_revisions import freeze_workflow_agent_revision
from porthouse.orchestration.task_graph import validate_and_order_graph
from porthouse.runtime.models import AgentOptions, GraphTaskSpec, TaskGraphSpec


def _required_text(value: Any, *, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValidationError(f"{field} is required")
    if len(normalized) > maximum:
        raise ValidationError(f"{field} must be at most {maximum} characters")
    return normalized


def _result_json(result: dict[str, Any] | None) -> dict[str, Any]:
    structured = (result or {}).get("structured_output")
    if isinstance(structured, dict):
        return dict(structured)
    content: Any = (result or {}).get("content")
    if isinstance(content, dict):
        return dict(content)
    if not isinstance(content, str):
        raise ValidationError("Workflow design Run returned no structured result")
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.S | re.I)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError("Workflow design Run returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValidationError("Workflow design result must be an object")
    return value


class WorkflowService:
    def __init__(self, runtime: Any, store: Any, *, default_agent_id: str) -> None:
        self.runtime = runtime
        self.store = store
        self.default_agent_id = default_agent_id

    def _catalog(
        self,
    ) -> tuple[set[str], set[str], dict[str, dict[str, str]], list[dict[str, Any]]]:
        profiles = self.store.list_agent_profiles()
        agents = {item.definition.agent_id for item in profiles}
        capabilities = self.store.list_capability_definitions()
        tools = {
            str(item.get("ref", {}).get("capability_id"))
            for item in capabilities
            if item.get("ref", {}).get("kind") in {"tool", "connector"}
        }
        skill_definitions = self.store.list_skills(active_only=True)
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
        public_catalog = [
            {
                "id": str(item.get("ref", {}).get("capability_id")),
                "kind": str(item.get("ref", {}).get("kind")),
                "name": str(item.get("name") or ""),
                "description": str(item.get("description") or "")[:500],
            }
            for item in capabilities
            if item.get("ref", {}).get("kind") in {"tool", "connector"}
        ]
        public_catalog.extend(
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
        return agents, tools, skills, public_catalog

    def _normalize_graph(self, value: dict[str, Any]) -> dict[str, Any]:
        agents, known_tools, known_skills, _ = self._catalog()
        requested_coordinator = str(
            value.get("coordinator_agent_id") or self.default_agent_id
        ).strip()
        coordinator_agent_id = (
            requested_coordinator
            if requested_coordinator in agents
            else self.default_agent_id
        )
        coordinator_agent_revision_id = freeze_workflow_agent_revision(
            self.store,
            coordinator_agent_id,
            (
                str(value.get("coordinator_agent_revision_id") or "").strip()
                or None
            ),
            field="Workflow coordinator",
        )
        source_nodes = value.get("nodes")
        if not isinstance(source_nodes, list) or not 1 <= len(source_nodes) <= 32:
            raise ValidationError("Workflow requires between 1 and 32 nodes")
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(source_nodes):
            if not isinstance(raw, dict):
                raise ValidationError("Workflow nodes must be objects")
            node_id = str(raw.get("id") or f"step-{index + 1}").strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", node_id):
                raise ValidationError(f"invalid Workflow node id: {node_id}")
            kind = str(raw.get("kind") or "agent")
            if kind not in {
                "agent",
                "team",
                "scenario",
                "approval",
                "verify",
                "branch",
                "bounded_loop",
            }:
                raise ValidationError(f"unsupported Workflow node kind: {kind}")
            objective = _required_text(
                raw.get("objective") or raw.get("prompt"), field=f"node {node_id} objective", maximum=2000
            )
            requested_agent = str(raw.get("agent_id") or "").strip()
            agent_id = requested_agent if requested_agent in agents else self.default_agent_id
            agent_revision_id: str | None = None
            dependencies = list(dict.fromkeys(str(item) for item in raw.get("dependencies") or []))
            allowed_tools = [
                item for item in dict.fromkeys(str(item) for item in raw.get("allowed_tools") or [])
                if item in known_tools
            ]
            skill_names = [
                item.removeprefix("skill.")
                for item in dict.fromkeys(str(item) for item in raw.get("skills") or [])
                if item.removeprefix("skill.") in known_skills
            ]
            configuration = dict(raw.get("configuration") or {})
            subrun: dict[str, Any] = {}
            if kind == "team":
                frozen = dict(raw.get("subrun") or {})
                team = (
                    self.store.get_agent_team_revision(
                        str(frozen.get("team_revision_id") or "")
                    )
                    if frozen.get("team_revision_id")
                    else self.store.get_published_agent_team(
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
                    "max_children_per_root": min(
                        256, max(1, int(configuration.get("max_children_per_root") or 32))
                    ),
                }
                agent_id = team.coordinator.agent_id
                agent_revision_id = team.coordinator.agent_revision_id
            elif kind == "scenario":
                frozen = dict(raw.get("subrun") or {})
                scenario_id = str(
                    frozen.get("scenario_id")
                    or raw.get("scenario_id")
                    or configuration.get("scenario_id")
                    or ""
                )
                version_value = (
                    frozen.get("scenario_version")
                    or raw.get("scenario_version")
                    or configuration.get("scenario_version")
                )
                scenario = self.store.get_scenario_version(
                    scenario_id,
                    int(version_value) if version_value is not None else None,
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
                agent_revision_id = freeze_workflow_agent_revision(
                    self.store,
                    agent_id,
                    (
                        str(
                            frozen.get("agent_revision_id")
                            or raw.get("agent_revision_id")
                            or ""
                        ).strip()
                        or None
                    ),
                    field=f"Workflow Scenario node {node_id}",
                )
                subrun = {
                    "mode": "scenario",
                    "scenario_id": scenario.scenario_id,
                    "scenario_version": scenario.version,
                    "planning_mode": scenario.planning_mode,
                    "agent_id": agent_id,
                    "agent_revision_id": agent_revision_id,
                    "inputs": inputs,
                    "max_children_per_root": min(
                        256, max(1, int(configuration.get("max_children_per_root") or 32))
                    ),
                }
            elif kind == "agent":
                agent_revision_id = freeze_workflow_agent_revision(
                    self.store,
                    agent_id,
                    str(raw.get("agent_revision_id") or "").strip() or None,
                    field=f"Workflow node {node_id}",
                )
            elif kind == "bounded_loop":
                template = dict(configuration.get("template") or {})
                template_type = str(
                    template.get("node_type")
                    or ("capability" if template.get("capability") else "agent")
                )
                if template_type == "agent":
                    template_agent_id = str(
                        template.get("agent_id") or coordinator_agent_id
                    ).strip()
                    if template_agent_id not in agents:
                        raise ValidationError(
                            f"Workflow bounded_loop node {node_id} Agent is not published"
                        )
                    template_metadata = dict(template.get("metadata") or {})
                    template_metadata["agent_revision_id"] = freeze_workflow_agent_revision(
                        self.store,
                        template_agent_id,
                        (
                            str(template_metadata.get("agent_revision_id") or "").strip()
                            or None
                        ),
                        field=f"Workflow bounded_loop node {node_id}",
                    )
                    template["agent_id"] = template_agent_id
                    template["metadata"] = template_metadata
                    configuration["template"] = template
            normalized.append(
                {
                    "id": node_id,
                    "name": _required_text(
                        raw.get("name") or node_id, field=f"node {node_id} name", maximum=128
                    ),
                    "objective": objective,
                    "kind": kind,
                    "agent_id": agent_id if kind in {"agent", "team", "scenario"} else None,
                    "agent_revision_id": agent_revision_id,
                    "dependencies": dependencies,
                    "allowed_tools": allowed_tools if kind == "agent" else [],
                    "skills": skill_names if kind == "agent" else [],
                    "skill_refs": (
                        [known_skills[name] for name in skill_names]
                        if kind == "agent"
                        else []
                    ),
                    "max_attempts": 1 if kind != "agent" else max(
                        1, min(int(raw.get("max_attempts") or 1), 5)
                    ),
                    "configuration": configuration,
                    "subrun": subrun,
                    "output_schema": (
                        dict(raw["output_schema"])
                        if isinstance(raw.get("output_schema"), dict)
                        else None
                    ),
                    "verification_policy": dict(raw.get("verification_policy") or {}),
                }
            )
        policies = dict(value.get("policies") or {})
        graph = {
            "schema_version": 1,
            "coordinator_agent_id": coordinator_agent_id,
            "coordinator_agent_revision_id": coordinator_agent_revision_id,
            "name": _required_text(value.get("name") or "智能工作流", field="name", maximum=128),
            "summary": str(value.get("summary") or "").strip()[:1000],
            "risk_level": (
                str(value.get("risk_level"))
                if str(value.get("risk_level")) in {"low", "medium", "high"}
                else "medium"
            ),
            "estimated_duration_minutes": max(
                0, min(int(value.get("estimated_duration_minutes") or 0), 10080)
            ),
            "nodes": normalized,
            "edges": [
                {"source": dependency, "target": node["id"]}
                for node in normalized
                for dependency in node["dependencies"]
            ],
            "policies": {
                "max_concurrent": max(1, min(int(policies.get("max_concurrent") or 4), 16)),
                "fail_fast": bool(policies.get("fail_fast", True)),
                "aggregate": bool(policies.get("aggregate", True)),
            },
        }
        try:
            validate_and_order_graph(self._graph_tasks(graph, goal=graph["summary"] or graph["name"]))
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return graph

    def _graph_tasks(self, graph: dict[str, Any], *, goal: str) -> list[GraphTaskSpec]:
        tasks: list[GraphTaskSpec] = []
        for node in graph["nodes"]:
            if node["kind"] == "approval":
                approval = {
                    "title": node["name"], "description": node["objective"],
                    "required_role": "owner", "risk": graph["risk_level"],
                    "data_classification": "internal", "expires_in_seconds": 86_400,
                    **dict(node.get("configuration") or {}),
                }
                tasks.append(
                    GraphTaskSpec(
                        id=node["id"], name=node["name"], prompt=node["objective"],
                        dependencies=list(node["dependencies"]), node_type="approval",
                        max_attempts=1,
                        approval=approval,
                    )
                )
                continue
            prompt = (
                f"Overall Workflow goal:\n{goal}\n\nAssigned objective:\n{node['objective']}\n\n"
                "Complete only this node and return a concise, verifiable result for downstream nodes."
            )
            if node["kind"] in {"team", "scenario"}:
                tasks.append(
                    GraphTaskSpec(
                        id=node["id"], name=node["name"], agent_id=node["agent_id"],
                        prompt=prompt, dependencies=list(node["dependencies"]),
                        node_type="subrun", max_attempts=1,
                        subrun=dict(node["subrun"]),
                        metadata={"workflow_node": node["id"]},
                    )
                )
                continue
            if node["kind"] in {"verify", "branch", "bounded_loop"}:
                configuration = dict(node.get("configuration") or {})
                tasks.append(
                    GraphTaskSpec(
                        id=node["id"], name=node["name"], prompt=node["objective"],
                        dependencies=list(node["dependencies"]), max_attempts=1,
                        node_type=node["kind"],
                        verify=configuration if node["kind"] == "verify" else {},
                        branch=configuration if node["kind"] == "branch" else {},
                        bounded_loop=(
                            configuration if node["kind"] == "bounded_loop" else {}
                        ),
                        output_schema=node.get("output_schema"),
                        verification_policy=dict(node.get("verification_policy") or {}),
                        metadata={"workflow_node": node["id"]},
                    )
                )
                continue
            tasks.append(
                GraphTaskSpec(
                    id=node["id"], name=node["name"], agent_id=node["agent_id"],
                    prompt=prompt,
                    dependencies=list(node["dependencies"]), max_attempts=node["max_attempts"],
                    allowed_tools=list(node["allowed_tools"]), skill_names=list(node["skills"]),
                    output_schema=node.get("output_schema"),
                    verification_policy=dict(node.get("verification_policy") or {}),
                    metadata={
                        "workflow_node": node["id"],
                        "agent_revision_id": node["agent_revision_id"],
                        "skill_refs": list(node.get("skill_refs") or []),
                    },
                )
            )
        return tasks

    async def list(self, context: RequestContext) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            self.store.list_user_workflows, user_id=context.user_id
        )
        for row in rows:
            row.pop("user_id", None)
        return rows

    async def get(self, context: RequestContext, workflow_id: str) -> dict[str, Any]:
        workflow = await asyncio.to_thread(
            self.store.get_user_workflow, workflow_id, expected_user_id=context.user_id
        )
        if workflow is None:
            raise NotFoundError("Workflow not found")
        revisions = await asyncio.to_thread(
            self.store.list_user_workflow_revisions, workflow_id, user_id=context.user_id
        )
        workflow["revisions"] = revisions
        workflow.pop("user_id", None)
        return workflow

    async def start_generation(
        self, context: RequestContext, value: dict[str, Any]
    ) -> Any:
        goal = _required_text(value.get("goal"), field="goal", maximum=4000)
        instruction = str(value.get("instruction") or "").strip()[:2000]
        base_graph = value.get("base_graph")
        workflow_id = str(value.get("workflow_id") or "").strip() or None
        if base_graph is None and workflow_id:
            workflow = await self.get(context, workflow_id)
            base_graph = workflow.get("revision", {}).get("graph")
        normalized_base = self._normalize_graph(dict(base_graph)) if base_graph else None
        agents, _, _, capabilities = await asyncio.to_thread(self._catalog)
        teams = [
            {
                "team_id": item.team_id,
                "revision_id": item.revision_id,
                "name": item.name,
                "description": item.description,
                "members": [
                    {"member_id": member.member_id, "role": member.role}
                    for member in item.members
                ],
            }
            for item in await asyncio.to_thread(self.store.list_agent_team_revisions)
            if item.status == "published"
        ]
        scenarios = [
            {
                "scenario_id": item.scenario_id,
                "version": item.version,
                "name": item.name,
                "required_inputs": [field.name for field in item.fields if field.required],
            }
            for item in await asyncio.to_thread(
                self.store.list_scenario_versions, published_only=True
            )
            if item.planning_mode == "fixed"
        ]
        requested_agent = str(value.get("agent_id") or self.default_agent_id)
        if requested_agent not in agents:
            raise ValidationError("Workflow designer Agent is not published")
        agent_catalog = [
            {
                "id": profile.definition.agent_id,
                "name": profile.definition.name,
                "role": profile.definition.role,
                "description": profile.definition.description,
            }
            for profile in await asyncio.to_thread(self.store.list_agent_profiles)
        ]
        prompt = (
            "Design a reusable executable Workflow for the user's goal. Return only schema-valid JSON. "
            "The user describes intent; you choose a small, clear DAG. Use dependencies to express order "
            "and parallelism. Use kind=team for open multi-expert collaboration, kind=scenario for a "
            "published fixed business execution, verify/branch/bounded_loop for deterministic quality "
            "control, and approval only for a genuine owner gate. Select only published IDs from the catalogs; "
            "never invent an ID. This is design-only: do not execute the work or call tools.\n\n"
            f"{WORKFLOW_CONTROL_GUIDE}\n"
            f"Goal:\n{goal}\n\nRequested change:\n{instruction or 'Create the first version.'}\n\n"
            f"Existing Workflow JSON:\n{json.dumps(normalized_base, ensure_ascii=False) if normalized_base else 'null'}\n\n"
            f"Agent catalog:\n{json.dumps(agent_catalog, ensure_ascii=False)[:12000]}\n\n"
            f"AgentTeam catalog:\n{json.dumps(teams, ensure_ascii=False)[:12000]}\n\n"
            f"Fixed Scenario catalog:\n{json.dumps(scenarios, ensure_ascii=False)[:12000]}\n\n"
            f"Capability catalog:\n{json.dumps(capabilities, ensure_ascii=False)[:20000]}"
        )
        return await self.runtime.submit_run(
            AgentOptions(
                prompt=prompt,
                user_id=context.user_id,
                session_id=f"workflow-design:{workflow_id or uuid4().hex[:12]}",
                agent_id=requested_agent,
                channel="workflow_studio",
                chat_id=workflow_id or "new-workflow",
                system_prompt=(
                    "You are Porthouse Workflow Designer. Produce concise executable DAG definitions, "
                    "not prose and not hidden reasoning. Never execute tools while designing."
                ),
                output_schema=WORKFLOW_DESIGN_SCHEMA,
                permission_mode="coordinator",
                allowed_tools=[],
                max_turns=1,
                metadata={
                    "source": "workflow_studio", "workflow_design": True,
                    "workflow_id": workflow_id, "design_goal": goal,
                    "coordinator_required": False,
                },
                idempotency_key=context.idempotency_key,
                request_id=context.request_id,
                tracker_id=context.tracker_id,
            )
        )

    async def generation(self, context: RequestContext, run_id: str) -> dict[str, Any]:
        run = await asyncio.to_thread(
            self.store.get_runtime_run, run_id, expected_user_id=context.user_id
        )
        if run is None or not bool(run.options.get("metadata", {}).get("workflow_design")):
            raise NotFoundError("Workflow generation not found")
        result: dict[str, Any] = {
            "run_id": run.run_id,
            "status": run.status,
            "status_summary": run.status_summary,
            "error": run.error,
        }
        if run.status == "completed":
            raw = _result_json(run.result)
            graph = await asyncio.to_thread(self._normalize_graph, raw)
            result["draft"] = {
                "name": graph["name"], "description": graph["summary"],
                "goal": str(run.options.get("metadata", {}).get("design_goal") or ""),
                "graph": graph,
            }
        return result

    async def save(
        self, context: RequestContext, workflow_id: str | None, value: dict[str, Any]
    ) -> dict[str, Any]:
        graph = await asyncio.to_thread(self._normalize_graph, dict(value["graph"]))
        name = _required_text(value.get("name") or graph["name"], field="name", maximum=128)
        goal = _required_text(value.get("goal"), field="goal", maximum=4000)
        values = {
            "workflow_id": workflow_id or f"wf_{uuid4().hex}",
            "revision_id": f"wfr_{uuid4().hex}",
            "user_id": context.user_id,
            "name": name,
            "description": str(value.get("description") or graph["summary"]).strip()[:1000],
            "goal": goal,
            "graph": graph,
            "change_note": str(value.get("change_note") or "").strip()[:1000],
            "source_run_id": value.get("source_run_id"),
        }
        if workflow_id is None:
            saved = await asyncio.to_thread(self.store.create_user_workflow, **values)
        else:
            saved = await asyncio.to_thread(self.store.create_user_workflow_revision, **values)
        if saved is None:
            raise NotFoundError("Workflow not found")
        return await self.get(context, values["workflow_id"])

    async def publish(
        self, context: RequestContext, workflow_id: str, revision_id: str
    ) -> dict[str, Any]:
        saved = await asyncio.to_thread(
            self.store.publish_user_workflow,
            workflow_id,
            revision_id,
            user_id=context.user_id,
        )
        if saved is None:
            raise NotFoundError("Workflow revision not found")
        return await self.get(context, workflow_id)

    async def delete(self, context: RequestContext, workflow_id: str) -> None:
        removed = await asyncio.to_thread(
            self.store.delete_user_workflow, workflow_id, user_id=context.user_id
        )
        if not removed:
            raise NotFoundError("Workflow not found")

    async def execute(
        self, context: RequestContext, workflow_id: str, value: dict[str, Any]
    ) -> Any:
        workflow = await self.get(context, workflow_id)
        revision_id = str(value.get("revision_id") or workflow.get("published_revision_id") or "")
        revision = next(
            (item for item in workflow["revisions"] if item["revision_id"] == revision_id), None
        )
        if revision is None:
            raise NotFoundError("Workflow revision not found")
        preview = bool(value.get("preview"))
        if revision["status"] not in {"published", "superseded"} and not preview:
            raise ConflictError("Workflow revision must be published before execution")
        graph = await asyncio.to_thread(self._normalize_graph, dict(revision["graph"]))
        input_text = str(value.get("input") or "").strip()
        goal = revision["goal"] + (f"\n\nExecution input:\n{input_text}" if input_text else "")
        policies = graph["policies"]
        session_id = str(value.get("session_id") or "").strip() or (
            f"workflow:{workflow_id[:48]}:{uuid4().hex[:12]}"
        )
        return await self.runtime.submit_graph(
            TaskGraphSpec(
                goal=goal,
                tasks=self._graph_tasks(graph, goal=goal),
                user_id=context.user_id,
                session_id=session_id,
                agent_id=graph["coordinator_agent_id"],
                agent_revision_id=graph["coordinator_agent_revision_id"],
                max_concurrent=policies["max_concurrent"],
                fail_fast=policies["fail_fast"],
                aggregate=policies["aggregate"],
                aggregation_policy={"mode": "llm_synthesis", "version": "v1"},
                input_asset_ids=list(value.get("input_asset_ids") or []),
                idempotency_key=context.idempotency_key,
                request_id=context.request_id,
                tracker_id=context.tracker_id,
                metadata={
                    **dict(value.get("metadata") or {}),
                    "source": "workflow",
                    "workflow_id": workflow_id,
                    "workflow_revision_id": revision_id,
                    "preview": preview,
                    "orchestration": {
                        "mode": "workflow",
                        "workflow_id": workflow_id,
                        "revision_id": revision_id,
                    },
                },
            )
        )
