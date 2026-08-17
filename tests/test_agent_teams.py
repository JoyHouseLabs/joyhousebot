from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from porthouse.api.app import create_app
from porthouse.application.agent_teams import AgentTeamService
from porthouse.application.errors import ConflictError
from porthouse.bootstrap.container import build_api_container
from porthouse.config.schema import Config
from porthouse.domain.agent_teams import AgentTeamMember, AgentTeamRevision
from porthouse.domain.collaboration_blueprints import frozen_enforced_blueprint
from porthouse.orchestration.blueprint_compiler import (
    BlueprintRepairError,
    PlanBoundaryViolationError,
    apply_blueprint_boundary,
    enforce_final_plan_boundary,
)
from porthouse.orchestration.coordinator_agent import normalize_coordinator_plan
from porthouse.orchestration.planner import build_coordinator_graph
from porthouse.runtime.models import AgentOptions
from porthouse.runtime.runner import NativeAgentRuntime
from tests.support.postgres_store import PostgresTestStore


def _team(*, status: str = "draft") -> AgentTeamRevision:
    return AgentTeamRevision(
        team_id="team.research",
        revision_id="team.research:v1",
        version=1,
        name="Research Team",
        description="A generic collaboration boundary used by an App.",
        coordinator_member_id="coordinator",
        members=(
            AgentTeamMember(
                member_id="coordinator",
                agent_id="default",
                agent_revision_id="default:v1",
                role="coordinator",
                responsibility="Decompose the goal, delegate work, and synthesize evidence.",
                can_delegate=True,
                allowed_handoffs=("researcher",),
            ),
            AgentTeamMember(
                member_id="researcher",
                agent_id="default",
                agent_revision_id="default:v1",
                role="researcher",
                responsibility="Research one bounded question and return evidence.",
            ),
        ),
        context_policy={
            "workspace_enabled": True,
            "default_visibility": "team",
            "max_entries": 10,
            "max_chars": 10000,
        },
        budget_policy={"max_tasks": 8, "max_parallel_tasks": 2, "max_handoffs": 4},
        status=status,
        created_by="admin",
    )


def _plan(member_id: str = "researcher") -> dict:
    return {
        "intent": "research",
        "summary": "Research and synthesize",
        "scenario_id": None,
        "scenario_inputs": {},
        "execution_class": "background",
        "estimated_duration_seconds": 120,
        "selected_capabilities": [],
        "selected_skills": [],
        "planned_steps": [
            {
                "id": "research",
                "name": "research",
                "objective": "Collect evidence",
                "phase": "research",
                "kind": "produce",
                "member_id": member_id,
                "depends_on": [],
                "acceptance_criteria": ["Evidence is attributable"],
            }
        ],
        "clarification": None,
    }


def test_team_contract_rejects_unknown_handoffs() -> None:
    with pytest.raises(ValueError, match="unknown handoffs"):
        AgentTeamRevision(
            team_id="team.invalid",
            revision_id="team.invalid:v1",
            version=1,
            name="Invalid",
            description="",
            coordinator_member_id="coordinator",
            members=(
                AgentTeamMember(
                    member_id="coordinator",
                    agent_id="default",
                    agent_revision_id="default:v1",
                    role="coordinator",
                    responsibility="Coordinate work.",
                    can_delegate=True,
                    allowed_handoffs=("missing",),
                ),
                AgentTeamMember(
                    member_id="worker",
                    agent_id="default",
                    agent_revision_id="default:v1",
                    role="worker",
                    responsibility="Do work.",
                ),
            ),
        )


def test_team_context_contract_freezes_required_and_excluded_layers() -> None:
    policy = _team().context_policy
    assert policy["required_context"] == [
        "root_goal",
        "team_identity",
        "assigned_objective",
        "confirmed_inputs",
        "dependency_results",
        "policy_snapshot",
    ]
    assert "member_private_memory" in policy["excluded_context"]
    assert "secrets" in policy["excluded_context"]
    assert policy["workspace_entry_types"] == ["task_result", "subagent_result"]
    assert "artifact_id" in policy["workspace_fields"]


def test_coordinator_plan_is_confined_to_team_members_and_budget() -> None:
    team = _team(status="published")
    normalized = normalize_coordinator_plan(_plan(), [], [], team=team)
    assert normalized["planned_steps"][0]["member_id"] == "researcher"

    graph = build_coordinator_graph(
        normalized,
        goal="Research this market",
        user_id="opc-user",
        session_id="session",
        agent_id="default",
        request_id="request",
        team=team,
        member_capabilities={"coordinator": set(), "researcher": set()},
        member_skills={"coordinator": set(), "researcher": set()},
        member_skill_refs={"coordinator": [], "researcher": []},
    )
    assert graph is not None
    assert len(graph.tasks) == 1
    assert graph.tasks[0].metadata["team_member_id"] == "researcher"
    assert graph.tasks[0].metadata["agent_revision_id"] == "default:v1"
    assert graph.max_concurrent == 1

    approval_team = AgentTeamRevision.from_dict(
        {
            **team.to_dict(),
            "approval_policy": {
                "require_result_approval": True,
                "required_role": "owner",
            },
        }
    )
    approval_graph = build_coordinator_graph(
        normalized,
        goal="Research this market",
        user_id="opc-user",
        session_id="session",
        agent_id="default",
        request_id="request",
        team=approval_team,
        member_capabilities={"coordinator": set(), "researcher": set()},
        member_skills={"coordinator": set(), "researcher": set()},
        member_skill_refs={"coordinator": [], "researcher": []},
    )
    assert approval_graph is not None
    assert approval_graph.tasks[-1].node_type == "approval"
    assert approval_graph.tasks[-1].dependencies == ["research"]

    with pytest.raises(ValueError, match="unknown AgentTeam member"):
        normalize_coordinator_plan(_plan("invented"), [], [], team=team)


@pytest.mark.asyncio
async def test_team_publication_and_workspace_are_durable_and_owner_scoped(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "agent-teams.db")
    service = AgentTeamService(store)
    saved = await service.save_draft(_team())
    assert saved["status"] == "draft"
    published = await service.publish(
        "team.research", "team.research:v1", actor_id="admin"
    )
    assert published["status"] == "published"

    team_ref = {
        "team_id": "team.research",
        "revision_id": "team.research:v1",
        "version": 1,
        "coordinator_member_id": "coordinator",
    }
    store.create_runtime_run(
        run_id="team-run",
        user_id="opc-user",
        session_id="session",
        agent_id="default",
        kind="graph",
        prompt="Research",
        options={
            "metadata": {
                "team_ref": team_ref,
                "team_members": [item.to_dict() for item in _team().members],
                "team_context_policy": dict(_team().context_policy),
            }
        },
    )
    store.create_runtime_task(
        task_id="team-run:research",
        run_id="team-run",
        agent_id="default",
        name="Research",
        payload={"prompt": "Research"},
        dependencies=[],
    )
    assert store.update_runtime_task(
        "team-run:research",
        status="completed",
        result={"content": "evidence"},
        workspace_entry={
            "entry_id": "teamws:research",
            "user_id": "opc-user",
            "root_run_id": "team-run",
            "team_id": "team.research",
            "team_revision_id": "team.research:v1",
            "source_run_id": "team-run",
            "source_task_id": "team-run:research",
            "member_id": "researcher",
            "entry_type": "task_result",
            "summary": "evidence",
            "data": {"content": "evidence"},
            "visibility": "coordinator",
        },
    )
    coordinator_entries = store.list_team_workspace_entries(
        user_id="opc-user",
        root_run_id="team-run",
        reader_member_id="coordinator",
        coordinator=True,
    )
    assert [item["summary"] for item in coordinator_entries] == ["evidence"]
    assert store.list_team_workspace_entries(
        user_id="other-user",
        root_run_id="team-run",
        reader_member_id="coordinator",
        coordinator=True,
    ) == []
    with pytest.raises(PermissionError, match="outside the frozen Team"):
        store.append_team_workspace_entry(
            entry_id="teamws:intruder",
            user_id="opc-user",
            root_run_id="team-run",
            team_id="team.research",
            team_revision_id="team.research:v1",
            source_run_id="team-run",
            source_task_id="team-run:research",
            member_id="intruder",
            entry_type="task_result",
            summary="must fail",
            data={},
        )


class _TeamAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def process_direct(
        self, content: str, *, run_context: Any, **_kwargs: Any
    ) -> str:
        self.prompts.append(content)
        if run_context.output_schema:
            return json.dumps(_plan())
        return f"evidence from {run_context.metadata.get('team_member_id')}: {content[:20]}"


@pytest.mark.asyncio
async def test_team_run_freezes_member_revision_and_materializes_workspace(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "agent-team-runtime.db")
    await AgentTeamService(store).save_draft(_team())
    await AgentTeamService(store).publish(
        "team.research", "team.research:v1", actor_id="admin"
    )
    team = store.get_published_agent_team("team.research")
    assert team is not None
    agent = _TeamAgent()
    runtime = NativeAgentRuntime(agent=agent, store=store)
    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="Research the durable Team runtime",
            user_id="opc-user",
            session_id="team-runtime",
            agent_id=team.coordinator.agent_id,
            agent_revision_id=team.coordinator.agent_revision_id,
            metadata={
                "coordinator_required": True,
                "team_ref": {
                    "team_id": team.team_id,
                    "revision_id": team.revision_id,
                    "version": team.version,
                    "coordinator_member_id": team.coordinator_member_id,
                },
                "team_members": [item.to_dict() for item in team.members],
                "team_member_id": team.coordinator_member_id,
                "team_context_policy": dict(team.context_policy),
                "team_budget_policy": dict(team.budget_policy),
                "team_approval_policy": dict(team.approval_policy),
            },
        )
    )
    completed = await runtime.wait(submitted.run_id, timeout=5)
    assert completed.status == "completed", (completed.error, completed.result)
    tasks = store.list_runtime_tasks(run_id=submitted.run_id)
    assert len(tasks) == 1
    assert tasks[0].payload["metadata"]["team_member_id"] == "researcher"
    assert tasks[0].payload["metadata"]["agent_revision_id"] == "default:v1"
    # Collaboration lineage (plan §4): every Task stays inside the root Run
    # and names its frozen team revision and responsible member.
    assert tasks[0].run_id == submitted.run_id
    assert tasks[0].payload["metadata"]["team_ref"]["revision_id"] == "team.research:v1"
    entries = store.list_team_workspace_entries(
        user_id="opc-user",
        root_run_id=submitted.run_id,
        reader_member_id="coordinator",
        coordinator=True,
    )
    assert len(entries) == 1
    assert entries[0]["member_id"] == "researcher"
    assert any("Frozen AgentTeam context" in item for item in agent.prompts)
    assert any('"responsibility"' in item for item in agent.prompts)
    assert any('"excluded_context"' in item for item in agent.prompts)
    await runtime.close()


def test_public_run_api_resolves_team_coordinator_and_freezes_revision(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "agent-team-api.db")
    store.save_agent_team_revision(_team())
    store.publish_agent_team_revision(
        "team.research", "team.research:v1", actor_id="admin"
    )
    store.create_api_access_token(
        user_id="opc-user", actor_id="test", token="team-api-token"
    )
    container = build_api_container(config=Config(), store=store)
    with TestClient(create_app(container)) as client:
        response = client.post(
            "/v1/runs",
            headers={"Authorization": "Bearer team-api-token"},
            json={
                "execution": {"mode": "team", "team_id": "team.research"},
                "input": {"type": "message", "content": "Research this market"},
            },
        )
    assert response.status_code == 202, response.text
    run = store.get_runtime_run(response.json()["run_id"])
    snapshot = store.get_run_execution_snapshot(response.json()["run_id"])
    assert run is not None and run.agent_id == "default"
    assert snapshot is not None and snapshot.agent_revision_id == "default:v1"
    assert run.options["metadata"]["team_ref"]["revision_id"] == "team.research:v1"
    assert store.list_team_workspace_entries(
        user_id="opc-user",
        root_run_id="team-run",
        reader_member_id="coordinator",
        coordinator=False,
    ) == []


def _blueprint_team() -> AgentTeamRevision:
    return AgentTeamRevision.from_dict(
        {
            **_team(status="published").to_dict(),
            "collaboration_blueprint": {
                "preset": "parallel_synthesize",
                "role_bindings": {"producers": ["researcher"]},
            },
        }
    )


def _synthesis_plan() -> dict:
    plan = _plan()
    plan["planned_steps"].append(
        {
            "id": "synthesis",
            "name": "synthesis",
            "objective": "Synthesize the evidence into one conclusion",
            "phase": "synthesize",
            "kind": "synthesize",
            "member_id": "coordinator",
            "depends_on": ["research"],
            "acceptance_criteria": ["Conclusion cites the evidence"],
        }
    )
    return plan


def test_explicit_blueprint_constrains_the_coordinator_plan() -> None:
    team = _blueprint_team()
    assert team.collaboration_blueprint is not None
    assert team.effective_blueprint["origin"] == "explicit"

    compliant = normalize_coordinator_plan(_synthesis_plan(), [], [], team=team)
    apply_blueprint_boundary(compliant, team.effective_blueprint, team=team)
    enforce_final_plan_boundary(compliant, team.effective_blueprint, team=team)

    rogue = normalize_coordinator_plan(
        _plan(),
        [],
        [],
        team=team,
    )
    rogue["planned_steps"][0]["kind"] = "review"
    rogue["planned_steps"][0]["review_of"] = ["research"]
    rogue["planned_steps"][0]["depends_on"] = []
    with pytest.raises(BlueprintRepairError):
        apply_blueprint_boundary(rogue, team.effective_blueprint, team=team)
    with pytest.raises(PlanBoundaryViolationError):
        enforce_final_plan_boundary(rogue, team.effective_blueprint, team=team)

    # Implicit defaults stay advisory: the runtime gate filters them out, so
    # the same plan shape on a legacy team never reaches the compiler.
    legacy = _team(status="published")
    assert frozen_enforced_blueprint(legacy.effective_blueprint) == {}
    assert frozen_enforced_blueprint(_blueprint_team().effective_blueprint)


@pytest.mark.asyncio
async def test_publish_creates_agent_team_rollout_and_migration_is_audited(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "agent-team-rollout.db")
    service = AgentTeamService(store)
    await service.save_draft(_team())
    await service.publish("team.research", "team.research:v1", actor_id="admin")

    rollout = store.get_latest_configuration_rollout("agent_team", "team.research")
    assert rollout is not None and rollout.revision_id == "team.research:v1"
    assert rollout.target_worker_count == 0 and rollout.status == "completed"

    migrated = await service.migrate_blueprint("team.research", actor_id="admin")
    assert migrated["status"] == "draft" and migrated["version"] == 2
    assert migrated["collaboration_blueprint"]["preset"] == "parallel_synthesize"
    published = store.get_agent_team_revision("team.research:v1")
    assert published is not None and published.collaboration_blueprint is None

    events = store.list_agent_team_events("team.research", limit=50)
    assert any(item["event_type"] == "blueprint_migrated" for item in events)

    with pytest.raises(ConflictError):
        await service.migrate_blueprint("team.research", actor_id="admin")
