"""Natural-language Workflow design, versioning, publication and execution."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from uuid import uuid4

from joyhousebot.application.context import RequestContext
from joyhousebot.application.errors import ConflictError, NotFoundError, ValidationError
from joyhousebot.application.workflow_capabilities import resolve_aggregation_policy
from joyhousebot.application.workflow_compiler import (
    WorkflowCompiler,
    compile_workflow_tasks,
    required_text,
)
from joyhousebot.application.workflow_contracts import (
    WORKFLOW_CONTROL_GUIDE,
    WORKFLOW_DESIGN_SCHEMA,
)
from joyhousebot.runtime.models import AgentOptions, GraphTaskSpec, TaskGraphSpec
from joyhousebot.storage.contracts import RuntimeStores


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
    def __init__(self, runtime: Any, store: object, *, default_agent_id: str) -> None:
        self.runtime = runtime
        self.stores = RuntimeStores.from_backend(store)
        self.default_agent_id = default_agent_id
        self.compiler = WorkflowCompiler(
            self.stores, default_agent_id=default_agent_id
        )

    def _catalog(
        self,
    ) -> tuple[set[str], set[str], dict[str, dict[str, str]], list[dict[str, Any]]]:
        catalog = self.compiler.catalog()
        return (
            set(catalog.agents),
            set(catalog.tools),
            catalog.skills,
            list(catalog.public),
        )

    def _normalize_graph(self, value: dict[str, Any]) -> dict[str, Any]:
        return self.compiler.normalize(value)

    @staticmethod
    def _graph_tasks(graph: dict[str, Any], *, goal: str) -> list[GraphTaskSpec]:
        return compile_workflow_tasks(graph, goal=goal)

    async def list(self, context: RequestContext) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(
            self.stores.workflows.list_user_workflows, user_id=context.user_id
        )
        for row in rows:
            row.pop("user_id", None)
        return rows

    async def get(self, context: RequestContext, workflow_id: str) -> dict[str, Any]:
        workflow = await asyncio.to_thread(
            self.stores.workflows.get_user_workflow,
            workflow_id,
            expected_user_id=context.user_id,
        )
        if workflow is None:
            raise NotFoundError("Workflow not found")
        revisions = await asyncio.to_thread(
            self.stores.workflows.list_user_workflow_revisions,
            workflow_id,
            user_id=context.user_id,
        )
        workflow["revisions"] = revisions
        workflow.pop("user_id", None)
        return workflow

    async def start_generation(
        self, context: RequestContext, value: dict[str, Any]
    ) -> Any:
        goal = required_text(value.get("goal"), field="goal", maximum=4000)
        instruction = str(value.get("instruction") or "").strip()[:2000]
        base_graph = value.get("base_graph")
        workflow_id = str(value.get("workflow_id") or "").strip() or None
        if base_graph is None and workflow_id:
            workflow = await self.get(context, workflow_id)
            base_graph = workflow.get("revision", {}).get("graph")
        normalized_base = self._normalize_graph(dict(base_graph)) if base_graph else None
        agents, _, _, capabilities = await asyncio.to_thread(self._catalog)
        teams = await self._generation_teams()
        scenarios = await self._generation_scenarios()
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
            for profile in await asyncio.to_thread(
                self.stores.catalog.list_agent_profiles
            )
        ]
        prompt = self._generation_prompt(
            goal=goal,
            instruction=instruction,
            normalized_base=normalized_base,
            agent_catalog=agent_catalog,
            teams=teams,
            scenarios=scenarios,
            capabilities=capabilities,
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
                    "You are joyhousebot Workflow Designer. Produce concise executable "
                    "DAG definitions, not prose and not hidden reasoning. Never execute "
                    "tools while designing."
                ),
                output_schema=WORKFLOW_DESIGN_SCHEMA,
                permission_mode="coordinator",
                allowed_tools=[],
                max_turns=1,
                metadata={
                    "source": "workflow_studio",
                    "workflow_design": True,
                    "workflow_id": workflow_id,
                    "design_goal": goal,
                    "coordinator_required": False,
                },
                idempotency_key=context.idempotency_key,
                request_id=context.request_id,
                tracker_id=context.tracker_id,
            )
        )

    async def _generation_teams(self) -> list[dict[str, Any]]:
        revisions = await asyncio.to_thread(
            self.stores.catalog.list_agent_team_revisions
        )
        return [
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
            for item in revisions
            if item.status == "published"
        ]

    async def _generation_scenarios(self) -> list[dict[str, Any]]:
        versions = await asyncio.to_thread(
            self.stores.scenarios.list_scenario_versions, published_only=True
        )
        return [
            {
                "scenario_id": item.scenario_id,
                "version": item.version,
                "name": item.name,
                "required_inputs": [
                    field.name for field in item.fields if field.required
                ],
            }
            for item in versions
            if item.planning_mode == "fixed"
        ]

    @staticmethod
    def _generation_prompt(
        *,
        goal: str,
        instruction: str,
        normalized_base: dict[str, Any] | None,
        agent_catalog: list[dict[str, Any]],
        teams: list[dict[str, Any]],
        scenarios: list[dict[str, Any]],
        capabilities: list[dict[str, Any]],
    ) -> str:
        return (
            "Design a reusable executable Workflow for the user's goal. Return only "
            "schema-valid JSON. The user describes intent; you choose a small, clear "
            "DAG. Use dependencies to express order and parallelism. Use kind=team for "
            "open multi-expert collaboration, kind=scenario for a published fixed "
            "business execution, verify/branch/bounded_loop for deterministic quality "
            "control, kind=capability for deterministic no-model steps that invoke one "
            "published capability directly, and approval only for a genuine owner gate. "
            "Select only published IDs from the catalogs; never invent an ID. This is "
            "design-only: do not execute the work or call tools.\n\n"
            f"{WORKFLOW_CONTROL_GUIDE}\n"
            f"Goal:\n{goal}\n\nRequested change:\n"
            f"{instruction or 'Create the first version.'}\n\n"
            "Existing Workflow JSON:\n"
            f"{json.dumps(normalized_base, ensure_ascii=False) if normalized_base else 'null'}\n\n"
            f"Agent catalog:\n{json.dumps(agent_catalog, ensure_ascii=False)[:12000]}\n\n"
            f"AgentTeam catalog:\n{json.dumps(teams, ensure_ascii=False)[:12000]}\n\n"
            f"Fixed Scenario catalog:\n{json.dumps(scenarios, ensure_ascii=False)[:12000]}\n\n"
            f"Capability catalog:\n{json.dumps(capabilities, ensure_ascii=False)[:20000]}"
        )

    async def generation(self, context: RequestContext, run_id: str) -> dict[str, Any]:
        run = await asyncio.to_thread(
            self.stores.runs.get_runtime_run,
            run_id,
            expected_user_id=context.user_id,
        )
        if run is None or not bool(
            run.options.get("metadata", {}).get("workflow_design")
        ):
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
                "name": graph["name"],
                "description": graph["summary"],
                "goal": str(
                    run.options.get("metadata", {}).get("design_goal") or ""
                ),
                "graph": graph,
            }
        return result

    async def save(
        self, context: RequestContext, workflow_id: str | None, value: dict[str, Any]
    ) -> dict[str, Any]:
        graph = await asyncio.to_thread(self._normalize_graph, dict(value["graph"]))
        name = required_text(
            value.get("name") or graph["name"], field="name", maximum=128
        )
        goal = required_text(value.get("goal"), field="goal", maximum=4000)
        values = {
            "workflow_id": workflow_id or f"wf_{uuid4().hex}",
            "revision_id": f"wfr_{uuid4().hex}",
            "user_id": context.user_id,
            "name": name,
            "description": str(
                value.get("description") or graph["summary"]
            ).strip()[:1000],
            "goal": goal,
            "graph": graph,
            "change_note": str(value.get("change_note") or "").strip()[:1000],
            "source_run_id": value.get("source_run_id"),
        }
        if workflow_id is None:
            saved = await asyncio.to_thread(
                self.stores.workflows.create_user_workflow, **values
            )
        else:
            saved = await asyncio.to_thread(
                self.stores.workflows.create_user_workflow_revision, **values
            )
        if saved is None:
            raise NotFoundError("Workflow not found")
        return await self.get(context, values["workflow_id"])

    async def publish(
        self, context: RequestContext, workflow_id: str, revision_id: str
    ) -> dict[str, Any]:
        saved = await asyncio.to_thread(
            self.stores.workflows.publish_user_workflow,
            workflow_id,
            revision_id,
            user_id=context.user_id,
        )
        if saved is None:
            raise NotFoundError("Workflow revision not found")
        return await self.get(context, workflow_id)

    async def delete(self, context: RequestContext, workflow_id: str) -> None:
        removed = await asyncio.to_thread(
            self.stores.workflows.delete_user_workflow,
            workflow_id,
            user_id=context.user_id,
        )
        if not removed:
            raise NotFoundError("Workflow not found")

    async def execute(
        self, context: RequestContext, workflow_id: str, value: dict[str, Any]
    ) -> Any:
        workflow = await self.get(context, workflow_id)
        revision_id = str(
            value.get("revision_id") or workflow.get("published_revision_id") or ""
        )
        revision = next(
            (
                item
                for item in workflow["revisions"]
                if item["revision_id"] == revision_id
            ),
            None,
        )
        if revision is None:
            raise NotFoundError("Workflow revision not found")
        preview = bool(value.get("preview"))
        if revision["status"] not in {"published", "superseded"} and not preview:
            raise ConflictError("Workflow revision must be published before execution")
        graph = await asyncio.to_thread(
            self._normalize_graph, dict(revision["graph"])
        )
        input_text = str(value.get("input") or "").strip()
        goal = revision["goal"] + (
            f"\n\nExecution input:\n{input_text}" if input_text else ""
        )
        policies = graph["policies"]
        session_id = str(value.get("session_id") or "").strip() or (
            f"workflow:{workflow_id[:48]}:{uuid4().hex[:12]}"
        )
        aggregation_policy = resolve_aggregation_policy(
            graph["nodes"], policies.get("aggregation")
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
                aggregation_policy=aggregation_policy,
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
