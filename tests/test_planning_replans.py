"""Bounded coordinator replanning, persistence, and recovery coverage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from porthouse.api.app import create_app
from porthouse.bootstrap.container import build_api_container
from porthouse.config.schema import Config
from porthouse.orchestration.coordinator_agent import normalize_coordinator_plan
from porthouse.runtime.context import CancellationToken, VerificationFailedError
from porthouse.runtime.models import AgentOptions, AgentUsage
from porthouse.runtime.planning_loop import run_coordinator_planning
from porthouse.runtime.runner import NativeAgentRuntime
from porthouse.storage.contracts import RuntimeStores
from tests.support.postgres_store import PostgresTestStore


def _valid_plan() -> dict[str, Any]:
    return {
        "intent": "research_and_compare",
        "summary": "Research two sources and combine the evidence",
        "scenario_id": None,
        "scenario_inputs": {},
        "execution_class": "background",
        "estimated_duration_seconds": 120,
        "selected_capabilities": [],
        "selected_skills": [],
        "planned_steps": [
            {
                "name": "source-a",
                "objective": "Research source A",
                "can_run_in_parallel": True,
            },
            {
                "name": "source-b",
                "objective": "Research source B",
                "can_run_in_parallel": True,
            },
        ],
        "clarification": None,
    }


class _PlanningAgent:
    def __init__(self, *, failures: int) -> None:
        self.failures = failures
        self.planning_calls = 0

    async def process_direct(self, content: str, *, run_context: Any, **_kwargs: Any) -> str:
        del content
        if run_context.output_schema:
            self.planning_calls += 1
            if self.planning_calls <= self.failures:
                return "not a structured plan"
            return json.dumps(_valid_plan())
        return "completed task evidence"


@pytest.fixture
def store(tmp_path: Path) -> PostgresTestStore:
    return PostgresTestStore(tmp_path / "planning-replans.db")


@pytest.mark.asyncio
async def test_invalid_plan_replans_then_materializes_graph(
    store: PostgresTestStore,
) -> None:
    agent = _PlanningAgent(failures=1)
    runtime = NativeAgentRuntime(agent=agent, store=store, max_concurrent_runs=2)
    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="Research and compare two sources",
            user_id="planner-user",
            session_id="planner-success",
            max_replans=1,
            metadata={"coordinator_required": True},
        )
    )

    completed = await runtime.wait(submitted.run_id, timeout=5)

    assert completed.status == "completed", (completed.error, completed.result)
    assert completed.kind == "graph"
    assert agent.planning_calls == 2
    decisions = store.list_loop_decisions(submitted.run_id)
    assert [item.decision for item in decisions] == ["replan", "continue"]
    assert [item.attempt for item in decisions] == [1, 2]
    assert decisions[0].reason_code == "plan_verification_failed"
    assert decisions[1].reason_code == "plan_accepted"
    assert decisions[1].details["replans_used"] == 1
    event_types = [item.type for item in store.list_runtime_events(submitted.run_id)]
    assert "plan.updated" in event_types
    assert event_types[-1] == "run.completed"
    await runtime.close()


@pytest.mark.asyncio
async def test_max_replans_exhaustion_is_an_explicit_terminal_failure(
    store: PostgresTestStore,
) -> None:
    agent = _PlanningAgent(failures=10)
    runtime = NativeAgentRuntime(agent=agent, store=store)
    submitted = await runtime.submit_run(
        AgentOptions(
            prompt="Build a plan that keeps failing validation",
            user_id="planner-user",
            session_id="planner-exhausted",
            max_replans=1,
            metadata={"coordinator_required": True},
        )
    )

    failed = await runtime.wait(submitted.run_id, timeout=5)

    assert failed.status == "failed"
    assert failed.result["stop_reason"] == "max_replans_exhausted"
    assert agent.planning_calls == 2
    decisions = store.list_loop_decisions(submitted.run_id)
    assert [item.decision for item in decisions] == ["replan", "escalate"]
    assert decisions[-1].reason_code == "max_replans_exhausted"
    assert decisions[-1].details["attempts"] == 2
    events = store.list_runtime_events(submitted.run_id)
    exhausted = [item for item in events if item.type == "loop.exhausted"]
    assert len(exhausted) == 1
    assert exhausted[0].data["stop_reason"] == "max_replans"
    await runtime.close()


class _CrashAfterReplanEvents:
    async def publish(self, event: Any) -> None:
        if event.type == "decision.recorded" and event.data.get("decision") == "replan":
            raise RuntimeError("simulated worker crash after durable replan decision")


class _CollectingEvents:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


class _PlanningRuntimeStub:
    def __init__(
        self,
        store: PostgresTestStore,
        *,
        worker_id: str,
        fail: bool,
        events: Any,
    ) -> None:
        self.stores = RuntimeStores.from_backend(store)
        self.worker_id = worker_id
        self.fail = fail
        self.events = events
        self.turn_scopes: list[str] = []

    async def _call_agent(self, **kwargs: Any) -> tuple[str, list[str], AgentUsage]:
        self.turn_scopes.append(str(kwargs["turn_scope"]))
        if self.fail:
            raise VerificationFailedError(
                (
                    {
                        "type": "schema",
                        "message": "plan schema failed",
                        "repairable": True,
                    },
                ),
                1,
            )
        return json.dumps(_valid_plan()), [], AgentUsage()


@pytest.mark.asyncio
async def test_worker_restart_resumes_after_persisted_replan_decision(
    store: PostgresTestStore,
) -> None:
    options = AgentOptions(
        prompt="Recover planning",
        user_id="planner-user",
        session_id="planner-recovery",
        max_replans=1,
    )
    store.create_runtime_run(
        run_id="planning-recovery",
        user_id=options.user_id,
        session_id=options.session_id,
        agent_id="default",
        kind="agent",
        prompt=options.prompt,
        options=options.to_dict(),
    )
    first_record = store.claim_runtime_run(
        "planning-recovery", worker_id="planner-a", lease_seconds=5
    )
    assert first_record is not None
    first = _PlanningRuntimeStub(
        store,
        worker_id="planner-a",
        fail=True,
        events=_CrashAfterReplanEvents(),
    )
    def normalize(value: dict[str, Any]) -> dict[str, Any]:
        return normalize_coordinator_plan(value, [], [])

    with pytest.raises(RuntimeError, match="simulated worker crash"):
        await run_coordinator_planning(
            first,
            record=first_record,
            options=options,
            cancellation=CancellationToken(),
            user_prompt=options.prompt,
            scenarios=[],
            capabilities=[],
            routing_decision={},
            normalize=normalize,
        )
    assert [item.decision for item in store.list_loop_decisions(first_record.run_id)] == [
        "replan"
    ]

    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            """UPDATE runtime_runs SET lease_expires_at=clock_timestamp()-interval '1 second'
               WHERE run_id=%s""",
            (first_record.run_id,),
        )
    second_record = store.claim_runtime_run(
        first_record.run_id, worker_id="planner-b", lease_seconds=30
    )
    assert second_record is not None
    second = _PlanningRuntimeStub(
        store,
        worker_id="planner-b",
        fail=False,
        events=_CollectingEvents(),
    )
    recovered = await run_coordinator_planning(
        second,
        record=second_record,
        options=options,
        cancellation=CancellationToken(),
        user_prompt=options.prompt,
        scenarios=[],
        capabilities=[],
        routing_decision={},
        normalize=normalize,
    )

    assert recovered.plan["intent"] == "research_and_compare"
    assert len(second.turn_scopes) == 1
    assert second.turn_scopes[0].endswith(":2")
    decisions = store.list_loop_decisions(second_record.run_id)
    assert [item.decision for item in decisions] == ["replan", "continue"]
    assert decisions[-1].run_lease_version == second_record.lease_version


def test_loop_decisions_are_owner_scoped_and_fence_stale_workers(
    store: PostgresTestStore,
) -> None:
    options = AgentOptions(
        prompt="Fence decisions",
        user_id="decision-owner",
        session_id="decision-session",
    )
    store.create_runtime_run(
        run_id="decision-fence",
        user_id=options.user_id,
        session_id=options.session_id,
        agent_id="default",
        kind="agent",
        prompt=options.prompt,
        options=options.to_dict(),
    )
    first = store.claim_runtime_run("decision-fence", worker_id="worker-a", lease_seconds=5)
    assert first is not None
    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            """UPDATE runtime_runs SET lease_expires_at=clock_timestamp()-interval '1 second'
               WHERE run_id='decision-fence'"""
        )
    second = store.claim_runtime_run("decision-fence", worker_id="worker-b")
    assert second is not None

    common = {
        "run_id": second.run_id,
        "task_id": None,
        "scope": "coordinator_plan:test",
        "attempt": 1,
        "decision": "continue",
        "reason_code": "plan_accepted",
        "summary": "accepted",
        "input_hash": "input-hash",
        "output_hash": "output-hash",
        "max_replans": 1,
        "details": {"plan": _valid_plan()},
    }
    stale = store.record_loop_decision(
        decision_id="decision-stale",
        decision_index=1,
        worker_id="worker-a",
        run_lease_version=first.lease_version,
        **common,
    )
    saved = store.record_loop_decision(
        decision_id="decision-current",
        decision_index=1,
        worker_id="worker-b",
        run_lease_version=second.lease_version,
        **common,
    )

    assert stale is None
    assert saved is not None
    assert store.list_loop_decisions(second.run_id, expected_user_id="other-user") == []
    owned = store.list_loop_decisions(
        second.run_id, expected_user_id="decision-owner"
    )
    assert [item.decision_id for item in owned] == ["decision-current"]


def test_loop_decision_api_is_owner_scoped_and_omits_internal_details(
    store: PostgresTestStore,
) -> None:
    store.create_api_access_token(
        user_id="decision-owner", actor_id="test", token="decision-token"
    )
    store.create_api_access_token(
        user_id="other-user", actor_id="test", token="other-token"
    )
    options = AgentOptions(
        prompt="Private planning prompt",
        user_id="decision-owner",
        session_id="decision-api",
    )
    store.create_runtime_run(
        run_id="decision-api-run",
        user_id=options.user_id,
        session_id=options.session_id,
        agent_id="default",
        kind="agent",
        prompt=options.prompt,
        options=options.to_dict(),
    )
    run = store.claim_runtime_run("decision-api-run", worker_id="api-worker")
    assert run is not None
    saved = store.record_loop_decision(
        decision_id="decision-api-record",
        run_id=run.run_id,
        task_id=None,
        scope="coordinator_plan:api",
        decision_index=1,
        attempt=1,
        decision="continue",
        reason_code="plan_accepted",
        summary="accepted",
        input_hash="input-hash",
        output_hash="output-hash",
        max_replans=2,
        details={"plan": _valid_plan(), "private_prompt": options.prompt},
        worker_id="api-worker",
        run_lease_version=run.lease_version,
    )
    assert saved is not None
    client = TestClient(
        create_app(build_api_container(config=Config(), store=store))
    )

    with client:
        own = client.get(
            f"/v1/runs/{run.run_id}/decisions",
            headers={"Authorization": "Bearer decision-token"},
        )
        other = client.get(
            f"/v1/runs/{run.run_id}/decisions",
            headers={"Authorization": "Bearer other-token"},
        )

    assert own.status_code == 200
    item = own.json()["items"][0]
    assert item["decision"] == "continue"
    assert "details" not in item
    assert "worker_id" not in item
    assert "run_lease_version" not in item
    assert other.status_code == 404
