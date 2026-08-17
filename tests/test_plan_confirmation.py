from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from porthouse.api.app import create_app
from porthouse.application.agent_teams import AgentTeamService
from porthouse.bootstrap.container import build_api_container
from porthouse.config.schema import Config
from porthouse.domain.agent_teams import AgentTeamMember, AgentTeamRevision
from porthouse.runtime.models import AgentOptions
from porthouse.runtime.runner import NativeAgentRuntime
from tests.support.postgres_store import PostgresTestStore

_USER = "opc-user"


def _confirmation_team() -> AgentTeamRevision:
    members = (
        AgentTeamMember(
            member_id="coordinator",
            agent_id="default",
            agent_revision_id="default:v1",
            role="coordinator",
            responsibility="Decompose, delegate, and synthesize.",
            can_delegate=True,
            allowed_handoffs=("researcher",),
        ),
        AgentTeamMember(
            member_id="researcher",
            agent_id="default",
            agent_revision_id="default:v1",
            role="researcher",
            responsibility="Produce the evidence draft.",
        ),
    )
    return AgentTeamRevision(
        team_id="team.confirm",
        revision_id="team.confirm:v1",
        version=1,
        name="Confirmation fixture",
        description="Team whose plans require owner confirmation.",
        coordinator_member_id="coordinator",
        members=members,
        budget_policy={"max_tasks": 8, "max_parallel_tasks": 2, "max_handoffs": 8},
        collaboration_blueprint={
            "preset": "parallel_synthesize",
            "role_bindings": {"producers": ["researcher"]},
            "guardrails": {
                "require_plan_confirmation": True,
                "require_review": False,
            },
        },
        status="draft",
        created_by="admin",
    )


def _plan(version: int = 1) -> dict[str, Any]:
    return {
        "intent": "research",
        "summary": f"Confirmation fixture plan v{version}",
        "scenario_id": None,
        "scenario_inputs": {},
        "execution_class": "background",
        "estimated_duration_seconds": 60,
        "selected_capabilities": [],
        "selected_skills": [],
        "planned_steps": [
            {
                "id": "research",
                "name": "research",
                "objective": "Collect the evidence",
                "phase": "produce",
                "kind": "produce",
                "member_id": "researcher",
                "depends_on": [],
                "acceptance_criteria": ["Evidence is attributable"],
            },
            {
                "id": "synthesis",
                "name": "synthesis",
                "objective": "Synthesize the conclusion",
                "phase": "synthesize",
                "kind": "synthesize",
                "member_id": "coordinator",
                "depends_on": ["research"],
                "acceptance_criteria": ["Conclusion cites evidence"],
            },
        ],
        "clarification": None,
    }


class _PlanningAgent:
    """Coordinator fake: emits versioned plans and records planning kwargs."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.planning_tool_locks: list[Any] = []

    async def process_direct(
        self, content: str, *, run_context: Any, **_kwargs: Any
    ) -> str:
        if run_context.output_schema:
            self.prompts.append(content)
            # Planning turns run tool-locked under the coordinator permission.
            self.planning_tool_locks.append(
                (getattr(run_context, "permission_mode", None), list(getattr(run_context, "allowed_tools", []) or []))
            )
            version = 2 if any("Prior plan user feedback" in item for item in self.prompts[:-1]) else 1
            return json.dumps(_plan(version))
        return f"evidence: {content[:24]}"


async def _wait_status(
    store: Any, run_id: str, statuses: set[str], timeout: float = 15.0
) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = store.get_runtime_run(run_id)
        if run is not None and str(run.status) in statuses:
            return run
        await asyncio.sleep(0.05)
    run = store.get_runtime_run(run_id)
    raise AssertionError(f"run stuck in {run.status if run else 'missing'}, wanted {statuses}")


async def _submit_confirmed_team_run(store: Any, agent: _PlanningAgent) -> Any:
    team = store.get_published_agent_team("team.confirm")
    assert team is not None
    runtime = NativeAgentRuntime(agent=agent, store=store)
    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="Produce a confirmed research summary",
            user_id=_USER,
            session_id="confirm-runtime",
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
                "team_collaboration_blueprint": dict(team.effective_blueprint),
            },
        )
    )
    return runtime, submitted


def _api(store: Any) -> TestClient:
    container = build_api_container(config=Config(), store=store)
    store.create_api_access_token(user_id=_USER, actor_id="test", token="plan-token")
    return TestClient(create_app(container))


async def _setup(tmp_path: Path) -> Any:
    store = PostgresTestStore(tmp_path / "plan-confirmation.db")
    await AgentTeamService(store).save_draft(_confirmation_team())
    await AgentTeamService(store).publish(
        "team.confirm", "team.confirm:v1", actor_id="admin"
    )
    return store


@pytest.mark.asyncio
async def test_plan_awaits_confirmation_then_materializes_in_same_run(
    tmp_path: Path,
) -> None:
    store = await _setup(tmp_path)
    agent = _PlanningAgent()
    runtime, submitted = await _submit_confirmed_team_run(store, agent)
    run_id = submitted.run_id
    await _wait_status(store, run_id, {"waiting_input"})

    confirmation = store.get_plan_confirmation(run_id)
    assert confirmation is not None and confirmation["status"] == "awaiting_confirmation"
    assert confirmation["plan_version"] == 1
    assert store.list_runtime_tasks(run_id=run_id) == []
    artifact_ids = {item["artifact_id"] for item in store.list_runtime_artifacts(run_id)}
    assert f"{run_id}:plan:v1" in artifact_ids and f"{run_id}:plan-spec:v1" in artifact_ids

    with _api(store) as client:
        preview = client.get(
            f"/v1/runs/{run_id}/plan", headers={"Authorization": "Bearer plan-token"}
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["awaiting_confirmation"] is True
        assert body["actions"] == ["confirm", "regenerate", "cancel"]
        assert [phase["id"] for phase in body["stage_graph"]["phases"]] == [
            "produce",
            "synthesize",
        ]
        assert body["estimate"]["task_count"] == 2

        confirmed = client.post(
            f"/v1/runs/{run_id}/plan/confirmation",
            headers={"Authorization": "Bearer plan-token"},
            json={"action": "confirm"},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["plan_confirmation"]["status"] == "confirmed"

    try:
        await _wait_status(store, run_id, {"completed"})
        tasks = store.list_runtime_tasks(run_id=run_id)
        assert [task.payload["metadata"]["team_member_id"] for task in tasks] == [
            "researcher",
            "coordinator",
        ]
        # Planning runs tool-locked: no external writes before confirmation.
        assert agent.planning_tool_locks
        permission_mode, planning_tools = agent.planning_tool_locks[0]
        assert permission_mode == "coordinator" and planning_tools == []
    finally:
        await asyncio.wait_for(runtime.close(), timeout=10)


@pytest.mark.asyncio
async def test_plan_regenerates_with_feedback_before_confirmation(
    tmp_path: Path,
) -> None:
    store = await _setup(tmp_path)
    agent = _PlanningAgent()
    runtime, submitted = await _submit_confirmed_team_run(store, agent)
    run_id = submitted.run_id
    await _wait_status(store, run_id, {"waiting_input"})

    # One API container for the whole interaction: a second in-process
    # container would start its own worker runtime and race for the Run.
    with _api(store) as client:
        rejected = client.post(
            f"/v1/runs/{run_id}/plan/confirmation",
            headers={"Authorization": "Bearer plan-token"},
            json={"action": "regenerate"},
        )
        assert rejected.status_code == 422, rejected.text

        regenerated = client.post(
            f"/v1/runs/{run_id}/plan/confirmation",
            headers={"Authorization": "Bearer plan-token"},
            json={"action": "regenerate", "feedback": "请聚焦教育场景"},
        )
        assert regenerated.status_code == 200, regenerated.text

        await _wait_status(store, run_id, {"waiting_input"})
        confirmation = store.get_plan_confirmation(run_id)
        assert confirmation is not None and confirmation["plan_version"] == 2
        artifacts = {item["artifact_id"] for item in store.list_runtime_artifacts(run_id)}
        assert f"{run_id}:plan-spec:v2" in artifacts
        assert any("Prior plan user feedback" in item and "请聚焦教育场景" in item for item in agent.prompts)
        assert store.list_runtime_tasks(run_id=run_id) == []

        confirmed = client.post(
            f"/v1/runs/{run_id}/plan/confirmation",
            headers={"Authorization": "Bearer plan-token"},
            json={"action": "confirm"},
        )
        assert confirmed.status_code == 200, confirmed.text
    try:
        await _wait_status(store, run_id, {"completed"})
        tasks = store.list_runtime_tasks(run_id=run_id)
        assert len(tasks) == 2
    finally:
        await asyncio.wait_for(runtime.close(), timeout=10)


@pytest.mark.asyncio
async def test_plan_cancel_creates_no_tasks_and_conflicts_afterward(
    tmp_path: Path,
) -> None:
    store = await _setup(tmp_path)
    agent = _PlanningAgent()
    runtime, submitted = await _submit_confirmed_team_run(store, agent)
    run_id = submitted.run_id
    await _wait_status(store, run_id, {"waiting_input"})

    with _api(store) as client:
        cancelled = client.post(
            f"/v1/runs/{run_id}/plan/confirmation",
            headers={"Authorization": "Bearer plan-token"},
            json={"action": "cancel"},
        )
        assert cancelled.status_code == 200, cancelled.text
        conflict = client.post(
            f"/v1/runs/{run_id}/plan/confirmation",
            headers={"Authorization": "Bearer plan-token"},
            json={"action": "confirm"},
        )
        assert conflict.status_code == 409, conflict.text

    try:
        await _wait_status(store, run_id, {"cancelled", "cancelling"})
        for _ in range(50):
            run = store.get_runtime_run(run_id)
            if str(run.status) == "cancelled":
                break
            await asyncio.sleep(0.1)
        assert store.list_runtime_tasks(run_id=run_id) == []
    finally:
        await asyncio.wait_for(runtime.close(), timeout=10)


@pytest.mark.asyncio
async def test_confirmed_plan_survives_worker_restart(tmp_path: Path) -> None:
    store = await _setup(tmp_path)
    first_agent = _PlanningAgent()
    runtime, submitted = await _submit_confirmed_team_run(store, first_agent)
    run_id = submitted.run_id
    await _wait_status(store, run_id, {"waiting_input"})
    await runtime.close()

    with _api(store) as client:
        confirmed = client.post(
            f"/v1/runs/{run_id}/plan/confirmation",
            headers={"Authorization": "Bearer plan-token"},
            json={"action": "confirm"},
        )
        assert confirmed.status_code == 200, confirmed.text

    replacement = NativeAgentRuntime(agent=_PlanningAgent(), store=store)
    await replacement.start()
    try:
        await _wait_status(store, run_id, {"completed"}, timeout=20.0)
        tasks = store.list_runtime_tasks(run_id=run_id)
        assert [task.payload["metadata"]["team_member_id"] for task in tasks] == [
            "researcher",
            "coordinator",
        ]
    finally:
        await asyncio.wait_for(replacement.close(), timeout=10)


@pytest.mark.asyncio
async def test_expired_plan_confirmation_fails_closed(tmp_path: Path) -> None:
    store = await _setup(tmp_path)
    agent = _PlanningAgent()
    runtime, submitted = await _submit_confirmed_team_run(store, agent)
    run_id = submitted.run_id
    try:
        await _wait_status(store, run_id, {"waiting_input"})
        with store._pool.connection() as conn:
            conn.execute(
                "UPDATE run_plan_confirmations SET expires_at=clock_timestamp()-interval '1 second' "
                "WHERE run_id=%s",
                (run_id,),
            )
        expired = store.expire_plan_confirmations()
        assert [item["run_id"] for item in expired] == [run_id]
        run = store.get_runtime_run(run_id)
        assert str(run.status) == "failed"
        assert run.status_reason == "plan_confirmation_expired"
        assert store.list_runtime_tasks(run_id=run_id) == []
    finally:
        await asyncio.wait_for(runtime.close(), timeout=10)


@pytest.mark.asyncio
async def test_plan_endpoints_isolate_foreign_owners(tmp_path: Path) -> None:
    store = await _setup(tmp_path)
    agent = _PlanningAgent()
    runtime, submitted = await _submit_confirmed_team_run(store, agent)
    run_id = submitted.run_id
    try:
        await _wait_status(store, run_id, {"waiting_input"})
        container = build_api_container(config=Config(), store=store)
        store.create_api_access_token(
            user_id="intruder", actor_id="test", token="intruder-token"
        )
        with TestClient(create_app(container)) as client:
            foreign_get = client.get(
                f"/v1/runs/{run_id}/plan",
                headers={"Authorization": "Bearer intruder-token"},
            )
            assert foreign_get.status_code == 404, foreign_get.text
            foreign_post = client.post(
                f"/v1/runs/{run_id}/plan/confirmation",
                headers={"Authorization": "Bearer intruder-token"},
                json={"action": "confirm"},
            )
            assert foreign_post.status_code == 404, foreign_post.text
        confirmation = store.get_plan_confirmation(run_id)
        assert confirmation is not None and confirmation["status"] == "awaiting_confirmation"
    finally:
        await asyncio.wait_for(runtime.close(), timeout=10)
