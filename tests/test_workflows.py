import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from porthouse.api.app import create_app
from porthouse.application.agent_teams import AgentTeamService
from porthouse.application.workflows import WorkflowService
from porthouse.bootstrap.container import build_api_container
from porthouse.config.schema import Config
from porthouse.domain.agent_teams import AgentTeamMember, AgentTeamRevision
from porthouse.domain.agents import AgentRevision
from porthouse.domain.scenarios import ScenarioField, ScenarioVersion
from porthouse.runtime.models import TaskGraphSpec
from porthouse.runtime.runner import NativeAgentRuntime
from tests.support.postgres_store import PostgresTestStore


def _client(tmp_path: Path) -> tuple[TestClient, PostgresTestStore]:
    store = PostgresTestStore(tmp_path / "workflows.db")
    store.create_api_access_token(user_id="user-a", actor_id="test", token="token-a")
    store.create_api_access_token(user_id="user-b", actor_id="test", token="token-b")
    return TestClient(create_app(build_api_container(config=Config(), store=store))), store


def _graph() -> dict:
    return {
        "name": "研究并发布方案",
        "summary": "完成研究、形成方案并在发布前由用户确认。",
        "risk_level": "medium",
        "estimated_duration_minutes": 30,
        "nodes": [
            {
                "id": "research",
                "name": "收集证据",
                "objective": "研究目标并保留可核验来源。",
                "kind": "agent",
                "agent_id": "default",
                "dependencies": [],
                "allowed_tools": [],
                "skills": [],
                "max_attempts": 2,
            },
            {
                "id": "draft",
                "name": "形成方案",
                "objective": "基于研究结果形成可执行方案。",
                "kind": "agent",
                "agent_id": "default",
                "dependencies": ["research"],
                "allowed_tools": [],
                "skills": [],
                "max_attempts": 1,
            },
            {
                "id": "approve",
                "name": "确认发布",
                "objective": "请用户确认方案是否可以发布。",
                "kind": "approval",
                "agent_id": None,
                "dependencies": ["draft"],
                "allowed_tools": [],
                "skills": [],
                "max_attempts": 1,
            },
        ],
        "policies": {"max_concurrent": 3, "fail_fast": True, "aggregate": True},
    }


def _teaching_team() -> AgentTeamRevision:
    member_specs = (
        ("coordinator", "coordinator", "Own the final teaching decision."),
        ("psychologist", "child psychologist", "Protect developmental suitability."),
        ("curriculum", "curriculum designer", "Design the learning sequence."),
        ("game", "game designer", "Design playful participation mechanics."),
        ("evaluator", "assessment expert", "Review evidence and measurable outcomes."),
    )
    return AgentTeamRevision(
        team_id="team.teaching-design",
        revision_id="team.teaching-design:v1",
        version=1,
        name="Teaching Design Council",
        description="Cross-disciplinary teaching-plan review and synthesis.",
        coordinator_member_id="coordinator",
        members=tuple(
            AgentTeamMember(
                member_id=member_id,
                agent_id="default",
                agent_revision_id="default:v1",
                role=role,
                responsibility=responsibility,
                can_delegate=member_id == "coordinator",
                allowed_handoffs=(
                    ("psychologist", "curriculum", "game", "evaluator")
                    if member_id == "coordinator"
                    else ()
                ),
            )
            for member_id, role, responsibility in member_specs
        ),
        budget_policy={
            "max_tasks": 10,
            "max_parallel_tasks": 4,
            "max_handoffs": 10,
            "max_review_rounds": 2,
        },
        status="draft",
        created_by="test",
    )


def _teaching_plan() -> dict[str, Any]:
    def step(
        step_id: str,
        member_id: str,
        kind: str,
        dependencies: list[str],
        *,
        review_of: list[str] | None = None,
        revision_of: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": step_id,
            "name": step_id.replace("-", " "),
            "objective": f"Complete the {step_id} contribution.",
            "phase": "design" if kind == "produce" else "quality",
            "kind": kind,
            "member_id": member_id,
            "depends_on": dependencies,
            "acceptance_criteria": ["The result is concrete and evidence-backed"],
            "review_of": review_of or [],
            "revision_of": revision_of,
            "review_round": 1 if kind in {"review", "revise"} else 0,
        }

    return {
        "intent": "teaching_design",
        "summary": "Design, review, revise, and synthesize a teaching plan.",
        "scenario_id": None,
        "scenario_inputs": {},
        "execution_class": "background",
        "estimated_duration_seconds": 300,
        "selected_capabilities": [],
        "selected_skills": [],
        "planned_steps": [
            step("psychology", "psychologist", "produce", []),
            step("curriculum", "curriculum", "produce", ["psychology"]),
            step("game-design", "game", "produce", ["psychology"]),
            step(
                "independent-review",
                "evaluator",
                "review",
                ["curriculum", "game-design"],
                review_of=["curriculum", "game-design"],
            ),
            step(
                "revision",
                "curriculum",
                "revise",
                ["curriculum", "independent-review"],
                revision_of="curriculum",
            ),
            step("synthesis", "coordinator", "synthesize", ["revision"]),
        ],
        "clarification": None,
    }


class _TeachingTeamAgent:
    async def process_direct(
        self, content: str, *, run_context: Any, **_kwargs: Any
    ) -> str:
        contract = dict(run_context.metadata.get("team_step_contract") or {})
        if contract.get("kind") == "review":
            return json.dumps(
                {
                    "verdict": "revise",
                    "issues": ["Assessment criteria need a measurable threshold"],
                    "required_changes": ["Add an observable success criterion"],
                    "evidence": ["curriculum", "game-design"],
                }
            )
        if run_context.output_schema:
            return json.dumps(_teaching_plan())
        return f"{run_context.metadata.get('team_member_id')}: {content[:80]}"


def _workflow_node(
    node_id: str,
    kind: str,
    dependencies: list[str],
    **values: Any,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "name": node_id.replace("-", " "),
        "objective": f"Complete {node_id} safely.",
        "kind": kind,
        "agent_id": "default" if kind == "agent" else None,
        "dependencies": dependencies,
        "allowed_tools": [],
        "skills": [],
        "max_attempts": 1,
        **values,
    }


def test_workflow_versions_publish_and_compile_to_runtime_graph(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    owner = {"Authorization": "Bearer token-a"}
    other = {"Authorization": "Bearer token-b"}
    payload = {
        "name": "研究并发布方案",
        "description": "从证据到经过确认的成果",
        "goal": "为新产品形成一份有证据支持的发布方案",
        "graph": _graph(),
        "change_note": "initial AI draft reviewed by user",
    }
    with client:
        created = client.post("/v1/workflows", headers=owner, json=payload)
        assert created.status_code == 201, created.text
        workflow = created.json()
        workflow_id = workflow["workflow_id"]
        first_revision = workflow["current_revision_id"]
        assert workflow["revision"]["version"] == 1
        assert workflow["revision"]["graph"]["edges"] == [
            {"source": "research", "target": "draft"},
            {"source": "draft", "target": "approve"},
        ]

        owner_list = client.get("/v1/workflows", headers=owner).json()["items"]
        assert owner_list[0]["workflow_id"] == workflow_id
        assert "user_id" not in owner_list[0]
        assert client.get("/v1/workflows", headers=other).json()["items"] == []
        assert client.get(f"/v1/workflows/{workflow_id}", headers=other).status_code == 404

        changed = {**payload, "change_note": "tighten the publish gate"}
        revised = client.post(
            f"/v1/workflows/{workflow_id}/revisions", headers=owner, json=changed
        )
        assert revised.status_code == 201, revised.text
        workflow = revised.json()
        second_revision = workflow["current_revision_id"]
        assert second_revision != first_revision
        assert [item["version"] for item in workflow["revisions"]] == [2, 1]

        unpublished = client.post(
            f"/v1/workflows/{workflow_id}/runs",
            headers=owner,
            json={"revision_id": second_revision},
        )
        assert unpublished.status_code == 409

        published = client.post(
            f"/v1/workflows/{workflow_id}/publish",
            headers=owner,
            json={"revision_id": second_revision},
        )
        assert published.status_code == 200, published.text
        assert published.json()["published_revision_id"] == second_revision

        started = client.post(
            "/v1/runs",
            headers={**owner, "Idempotency-Key": "workflow-run-1"},
            json={
                "execution": {
                    "mode": "workflow",
                    "workflow_id": workflow_id,
                    "revision_id": second_revision,
                },
                "session_id": "workflow-common-entry",
                "input": {
                    "type": "message",
                    "content": "重点关注可验证的用户价值",
                },
                "metadata": {"app": {"installation_id": "appinst-test"}},
            },
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["run_id"]
        run = store.get_runtime_run(run_id, expected_user_id="user-a")
        assert run is not None
        assert run.kind == "graph"
        assert run.session_id == "workflow-common-entry"
        assert run.options["metadata"]["workflow_id"] == workflow_id
        assert run.options["metadata"]["app"]["installation_id"] == "appinst-test"
        assert run.options["metadata"]["orchestration"]["mode"] == "workflow"
        tasks = client.get(f"/v1/runs/{run_id}/tasks", headers=owner).json()["items"]
        assert len(tasks) == 3
        assert {item["payload"]["node_type"] for item in tasks} == {"agent", "approval"}

        assert client.delete(f"/v1/workflows/{workflow_id}", headers=other).status_code == 404
        assert client.delete(f"/v1/workflows/{workflow_id}", headers=owner).status_code == 204


def test_workflow_generation_is_design_only_and_invalid_graphs_are_rejected(
    tmp_path: Path,
) -> None:
    client, store = _client(tmp_path)
    owner = {"Authorization": "Bearer token-a"}
    invalid = _graph()
    invalid["nodes"][0]["dependencies"] = ["draft"]
    payload = {
        "name": "invalid",
        "goal": "must fail",
        "graph": invalid,
    }
    with client:
        rejected = client.post("/v1/workflows", headers=owner, json=payload)
        assert rejected.status_code == 422
        assert "cycle" in rejected.json()["error"]["message"]

        submitted = client.post(
            "/v1/workflows/generations",
            headers={**owner, "Idempotency-Key": "workflow-design-1"},
            json={"goal": "每天汇总重要信息，生成一份需要我确认的简报"},
        )
        assert submitted.status_code == 202, submitted.text
        run_id = submitted.json()["run_id"]
        run = store.get_runtime_run(run_id, expected_user_id="user-a")
        assert run is not None
        assert run.options["metadata"]["workflow_design"] is True
        assert run.options["metadata"]["coordinator_required"] is False
        assert run.options["permission_mode"] == "coordinator"
        assert run.options["allowed_tools"] == []
        assert run.options["output_schema"]["properties"]["nodes"]["maxItems"] == 32

        generation = client.get(
            f"/v1/workflows/generations/{run_id}", headers=owner
        )
        assert generation.status_code == 200
        assert generation.json()["status"] == "queued"
        assert (
            client.get(f"/v1/workflows/generations/{run_id}", headers={"Authorization": "Bearer token-b"}).status_code
            == 404
        )


def test_workflow_compiles_verification_branch_and_bounded_loop_nodes(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "workflow-controls.db")
    service = WorkflowService(None, store, default_agent_id="default")
    route_schema = {
        "type": "object",
        "properties": {"route": {"type": "string", "enum": ["publish", "review"]}},
        "required": ["route"],
        "additionalProperties": False,
    }
    routed = service._normalize_graph(
        {
            "name": "Quality-routed workflow",
            "summary": "Verify structured output, then choose one declared path.",
            "risk_level": "medium",
            "estimated_duration_minutes": 5,
            "nodes": [
                _workflow_node("source", "agent", [], output_schema=route_schema),
                _workflow_node(
                    "verify",
                    "verify",
                    ["source"],
                    configuration={"source": "tasks.source"},
                    output_schema=route_schema,
                ),
                _workflow_node(
                    "route",
                    "branch",
                    ["verify"],
                    configuration={
                        "source": "tasks.verify",
                        "path": "structured_output.route",
                        "cases": [
                            {
                                "when": {"op": "eq", "value": "publish"},
                                "targets": ["publish"],
                            }
                        ],
                        "default_targets": ["review"],
                    },
                ),
                _workflow_node("publish", "agent", ["route"]),
                _workflow_node("review", "agent", ["route"]),
            ],
            "policies": {"max_concurrent": 2, "fail_fast": True, "aggregate": False},
        }
    )
    assert [task.node_type for task in service._graph_tasks(routed, goal="route")] == [
        "agent",
        "verify",
        "branch",
        "agent",
        "agent",
    ]

    state_schema = {
        "type": "object",
        "properties": {
            "value": {"type": "integer"},
            "done": {"type": "boolean"},
        },
        "required": ["value", "done"],
        "additionalProperties": False,
    }
    looped = service._normalize_graph(
        {
            "name": "Bounded improvement workflow",
            "summary": "Improve a verified state until ready or the explicit bound is reached.",
            "risk_level": "low",
            "estimated_duration_minutes": 10,
            "nodes": [
                _workflow_node("state", "agent", [], output_schema=state_schema),
                _workflow_node(
                    "improve",
                    "bounded_loop",
                    ["state"],
                    configuration={
                        "source": "tasks.state",
                        "path": "structured_output",
                        "state_path": "structured_output",
                        "max_iterations": 3,
                        "exit": {
                            "path": "structured_output.done",
                            "when": {"op": "eq", "value": True},
                        },
                        "template": {
                            "node_type": "agent",
                            "prompt": "Improve ${state.value} in round ${iteration.number}",
                            "output_schema": state_schema,
                            "max_attempts": 1,
                        },
                    },
                ),
                _workflow_node("finish", "agent", ["improve"]),
            ],
            "policies": {"max_concurrent": 1, "fail_fast": True, "aggregate": False},
        }
    )
    loop = service._graph_tasks(looped, goal="improve")[1]
    assert loop.node_type == "bounded_loop"
    assert loop.bounded_loop["max_iterations"] == 3


@pytest.mark.asyncio
async def test_workflow_team_node_runs_a_frozen_recoverable_child_run(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "workflow-team-subrun.db")
    teams = AgentTeamService(store)
    await teams.save_draft(_teaching_team())
    await teams.publish(
        "team.teaching-design", "team.teaching-design:v1", actor_id="test"
    )
    runtime = NativeAgentRuntime(agent=_TeachingTeamAgent(), store=store)
    service = WorkflowService(runtime, store, default_agent_id="default")
    graph = service._normalize_graph(
        {
            "name": "Reviewed teaching plan",
            "summary": "Ask a cross-disciplinary Team to produce a reviewed plan.",
            "risk_level": "medium",
            "estimated_duration_minutes": 20,
            "nodes": [
                {
                    "id": "teaching-team",
                    "name": "Teaching design council",
                    "objective": "Create an age-appropriate, playful, measurable plan.",
                    "kind": "team",
                    "team_id": "team.teaching-design",
                    "agent_id": None,
                    "dependencies": [],
                    "allowed_tools": [],
                    "skills": [],
                    "max_attempts": 1,
                }
            ],
            "policies": {
                "max_concurrent": 2,
                "fail_fast": True,
                "aggregate": False,
            },
        }
    )
    frozen = graph["nodes"][0]["subrun"]
    assert frozen["team_revision_id"] == "team.teaching-design:v1"
    assert frozen["coordinator_agent_revision_id"] == "default:v1"

    submitted = await runtime.submit_graph(
        TaskGraphSpec(
            goal="Design a teaching plan for a seven-year-old learner.",
            tasks=service._graph_tasks(graph, goal="Design a teaching plan."),
            user_id="opc-user",
            session_id="workflow-team",
            agent_id="default",
            max_concurrent=2,
            aggregate=False,
        )
    )
    try:
        for _ in range(100):
            completed = await runtime.wait(submitted.run_id, timeout=1)
            if completed.status in {"completed", "failed", "cancelled", "timed_out"}:
                break
            await asyncio.sleep(0.05)
    finally:
        await runtime.close()
    assert completed.status == "completed", (completed.error, completed.result)
    parent_task = store.list_runtime_tasks(run_id=submitted.run_id)[0]
    child_run_id = parent_task.result["child_run_id"]
    child = store.get_runtime_run(child_run_id)
    assert parent_task.status == "completed", (
        parent_task.error,
        parent_task.result,
        child.error if child else None,
        [
            (item.payload.get("spec_id"), item.status, item.error)
            for item in store.list_runtime_tasks(run_id=child_run_id)
        ],
    )
    assert child is not None and child.status == "completed"
    assert child.parent_run_id == submitted.run_id
    assert child.parent_task_id == parent_task.task_id
    child_tasks = store.list_runtime_tasks(run_id=child_run_id)
    assert [item.payload["spec_id"] for item in child_tasks] == [
        "psychology",
        "curriculum",
        "game-design",
        "independent-review",
        "revision",
        "synthesis",
    ]
    review = next(
        item for item in child_tasks if item.payload["spec_id"] == "independent-review"
    )
    assert review.result["structured_output"]["verdict"] == "revise"
    assert completed.root_run_id == submitted.run_id


@pytest.mark.asyncio
async def test_workflow_scenario_node_runs_the_frozen_fixed_revision(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "workflow-scenario-subrun.db")
    store.save_scenario_version(
        ScenarioVersion(
            scenario_id="scenario.lesson-brief",
            version=1,
            name="Lesson brief",
            description="Create a fixed lesson brief from validated input.",
            fields=(ScenarioField("topic", "string", required=True),),
            nodes=(),
            edges=(),
            allowed_capabilities=(),
            planning_mode="fixed",
            execution_policy={
                "aggregate": False,
                "tasks": [
                    {
                        "id": "write-brief",
                        "name": "Write lesson brief",
                        "prompt": "Write a lesson brief about ${topic}",
                    }
                ],
            },
        ),
        status="published",
    )
    runtime = NativeAgentRuntime(agent=_TeachingTeamAgent(), store=store)
    service = WorkflowService(runtime, store, default_agent_id="default")
    graph = service._normalize_graph(
        {
            "name": "Fixed lesson workflow",
            "summary": "Run one published lesson Scenario.",
            "risk_level": "low",
            "estimated_duration_minutes": 2,
            "nodes": [
                _workflow_node(
                    "lesson",
                    "scenario",
                    [],
                    scenario_id="scenario.lesson-brief",
                    scenario_version=1,
                    scenario_inputs={"topic": "fractions"},
                )
            ],
            "policies": {"max_concurrent": 1, "fail_fast": True, "aggregate": False},
        }
    )
    frozen = graph["nodes"][0]["subrun"]
    assert frozen["scenario_version"] == 1
    assert frozen["agent_revision_id"] == "default:v1"
    definition = store.get_agent_definition("default")
    assert definition is not None
    store.save_agent_revision(
        definition,
        AgentRevision(
            revision_id="default:v2",
            agent_id="default",
            version=2,
            instructions="This newer revision must not change the frozen Workflow.",
            model_policy={"primary": "test/new-default"},
            status="published",
        ),
    )
    submitted = await runtime.submit_graph(
        TaskGraphSpec(
            goal="Prepare the lesson.",
            tasks=service._graph_tasks(graph, goal="Prepare the lesson."),
            user_id="opc-user",
            session_id="workflow-scenario",
            agent_id=graph["coordinator_agent_id"],
            agent_revision_id=graph["coordinator_agent_revision_id"],
            aggregate=False,
        )
    )
    try:
        for _ in range(100):
            completed = await runtime.wait(submitted.run_id, timeout=1)
            if completed.status in {"completed", "failed", "cancelled", "timed_out"}:
                break
            await asyncio.sleep(0.05)
    finally:
        await runtime.close()
    assert completed.status == "completed", (completed.error, completed.result)
    parent_task = store.list_runtime_tasks(run_id=submitted.run_id)[0]
    assert parent_task.status == "completed", (parent_task.error, parent_task.result)
    child = store.get_runtime_run(parent_task.result["child_run_id"])
    assert child is not None and child.status == "completed"
    child_snapshot = store.get_run_execution_snapshot(child.run_id)
    assert child_snapshot is not None
    assert child_snapshot.agent_revision_id == "default:v1"
    assert child.parent_run_id == submitted.run_id
    child_task = store.list_runtime_tasks(run_id=child.run_id)[0]
    assert child_task.payload["metadata"]["scenario_version"] == 1
    assert "fractions" in child_task.payload["prompt"]
