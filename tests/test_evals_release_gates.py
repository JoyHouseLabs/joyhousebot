"""Deterministic Eval evidence gates catalog publication."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.application.errors import ConflictError
from joyhousebot.application.eval_execution import EvalExecutionService
from joyhousebot.application.evals import EvalService
from joyhousebot.application.scenarios import ScenarioStudioService
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.domain.agents import AgentDefinition, AgentRevision
from joyhousebot.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
)
from joyhousebot.domain.scenarios import ScenarioVersion
from joyhousebot.runtime.runner import NativeAgentRuntime
from tests.support.postgres_store import PostgresTestStore


def _suite_body() -> dict:
    return {
        "suite_id": "quality.basic",
        "version": 1,
        "name": "Basic quality and budget gate",
        "target_types": ["agent", "scenario", "capability"],
        "thresholds": {
            "min_pass_rate": 1.0,
            "min_average_score": 1.0,
            "max_total_cost_usd": 0.01,
            "max_p95_latency_ms": 500,
            "min_cost_coverage": 1.0,
        },
        "cases": [
            {
                "case_id": "answer",
                "name": "Returns a valid answer",
                "input": {"prompt": "answer yes"},
                "expected": "yes",
                "scorers": [
                    {"type": "status", "value": "completed"},
                    {
                        "type": "json_schema",
                        "schema": {
                            "type": "object",
                            "properties": {"answer": {"type": "string"}},
                            "required": ["answer"],
                            "additionalProperties": False,
                        },
                    },
                    {"type": "json_path_equals", "path": "answer", "value": "yes"},
                    {"type": "max_latency_ms", "value": 500},
                    {"type": "max_cost_usd", "value": 0.01},
                ],
            }
        ],
    }


def _admin_client(store: PostgresTestStore) -> tuple[TestClient, dict[str, str]]:
    store.upsert_platform_admin(
        user_id="quality-admin",
        role="admin",
        permissions=["*"],
        actor_id="bootstrap",
    )
    store.create_api_access_token(
        user_id="quality-admin", actor_id="bootstrap", token="quality-admin-token"
    )
    container = build_api_container(config=Config(), store=store)
    return TestClient(create_app(container)), {
        "Authorization": "Bearer quality-admin-token"
    }


class _EvalAgent:
    async def process_direct(self, content: str, **_kwargs: Any) -> str:
        return f"verified evidence: {content}"


@pytest.mark.asyncio
async def test_automated_eval_executes_and_snapshots_exact_draft_agent_revision(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "eval-execution.db")
    definition = AgentDefinition(
        agent_id="research-agent",
        name="Research Agent",
        role="specialist",
    )
    revision = AgentRevision(
        revision_id="research-agent:v2",
        agent_id="research-agent",
        version=2,
        status="draft",
        model_policy={"primary": "test/model"},
    )
    store.save_agent_revision(definition, revision)
    evals = EvalService(store)
    await evals.save_suite(
        {
            "suite_id": "research.evidence",
            "version": 1,
            "name": "Research evidence contract",
            "target_types": ["agent"],
            "thresholds": {"min_pass_rate": 1, "min_average_score": 1},
            "cases": [
                {
                    "case_id": "evidence",
                    "name": "Produces traceable evidence",
                    "input": {"prompt": "find the source"},
                    "scorers": [
                        {"type": "status", "value": "completed"},
                        {
                            "type": "contains",
                            "path": "result.content",
                            "value": "verified evidence",
                        },
                        {"type": "json_path_exists", "path": "runtime_run_id"},
                        {
                            "type": "not_contains",
                            "path": "result.content",
                            "value": "SECRET_VALUE",
                        },
                    ],
                }
            ],
        },
        actor_id="quality-admin",
    )
    eval_run = await evals.create_run(
        {
            "suite_id": "research.evidence",
            "suite_version": 1,
            "target_type": "agent",
            "target_id": "research-agent",
            "target_revision_id": "research-agent:v2",
            "idempotency_key": "research-agent-v2",
        },
        actor_id="quality-admin",
    )
    runtime = NativeAgentRuntime(agent=_EvalAgent(), store=store)
    executor = EvalExecutionService(
        store=store,
        runtime=runtime,
        evals=evals,
        scenarios=ScenarioStudioService(store),
    )
    finalized = await executor.execute(
        eval_run["eval_run_id"], actor_id="quality-admin", case_timeout_seconds=5
    )

    assert finalized["status"] == "passed"
    source_run_id = finalized["results"][0]["metrics"]["source_run_id"]
    snapshot = store.get_run_execution_snapshot(source_run_id)
    assert snapshot is not None
    assert snapshot.agent_revision_id == "research-agent:v2"
    assert store.get_agent_revision("research-agent:v2").status == "draft"
    await evals.save_release_gate(
        {
            "target_type": "agent",
            "target_id": "research-agent",
            "target_revision_id": "research-agent:v2",
            "required": True,
            "requirements": [
                {
                    "suite_id": "research.evidence",
                    "suite_version": 1,
                    "min_pass_rate": 1,
                    "max_age_hours": 24,
                    "require_automated": True,
                }
            ],
        },
        actor_id="quality-admin",
    )
    gate = store.evaluate_release_gate(
        target_type="agent",
        target_id="research-agent",
        target_revision_id="research-agent:v2",
        purpose="automated-evidence-test",
        actor_id="quality-admin",
        decision_id="gate-automated-evidence",
    )
    assert gate["passed"] is True
    assert gate["requirements"][0]["automated"] is True
    await runtime.close()


@pytest.mark.asyncio
async def test_checked_in_business_eval_suites_are_valid(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "business-eval-suites.db")
    evals = EvalService(store)
    suite_paths = sorted(
        (Path(__file__).resolve().parents[1] / "evals" / "suites").glob("*.json")
    )
    assert len(suite_paths) == 3
    installed = []
    for path in suite_paths:
        installed.append(
            await evals.save_suite(
                json.loads(path.read_text(encoding="utf-8")),
                actor_id="suite-validation",
            )
        )
    assert {item["suite_id"] for item in installed} == {
        "business.governed-execution",
        "business.publishable-work",
        "business.research-evidence",
    }
    assert sum(len(item["cases"]) for item in installed) == 9


@pytest.mark.asyncio
async def test_eval_execution_jobs_are_leased_fenced_and_resumable(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "eval-jobs.db")
    evals = EvalService(store)
    await evals.save_suite(_suite_body(), actor_id="quality-admin")
    run = await evals.create_run(
        {
            "suite_id": "quality.basic",
            "suite_version": 1,
            "target_type": "agent",
            "target_id": "quality-agent",
            "target_revision_id": "quality-agent:v1",
            "idempotency_key": "leased-job",
        },
        actor_id="quality-admin",
    )
    queued = store.enqueue_eval_execution(
        run["eval_run_id"],
        configuration={"max_concurrency": 2, "case_timeout_seconds": 30},
        requested_by="quality-admin",
    )
    assert queued["status"] == "queued"
    first = store.claim_eval_execution_job(worker_id="eval-worker-a", lease_seconds=30)
    assert first and first["attempt"] == 1
    with store._pool.connection() as conn:
        conn.execute(
            """UPDATE eval_execution_jobs
               SET lease_expires_at=clock_timestamp()-interval '1 second'
               WHERE eval_run_id=%s""",
            (run["eval_run_id"],),
        )
    second = store.claim_eval_execution_job(worker_id="eval-worker-b", lease_seconds=30)
    assert second and second["lease_version"] == first["lease_version"] + 1
    assert not store.complete_eval_execution_job(
        run["eval_run_id"],
        worker_id="eval-worker-a",
        lease_version=first["lease_version"],
    )
    assert store.fail_eval_execution_job(
        run["eval_run_id"],
        worker_id="eval-worker-b",
        lease_version=second["lease_version"],
        error={"type": "TransientError"},
    )
    assert store.get_eval_execution_job(run["eval_run_id"])["status"] == "queued"


@pytest.mark.asyncio
async def test_due_eval_schedule_materializes_one_idempotent_run_and_job(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "eval-schedules.db")
    evals = EvalService(store)
    await evals.save_suite(_suite_body(), actor_id="quality-admin")
    policy = await evals.save_schedule(
        {
            "policy_id": "quality-agent-nightly",
            "suite_id": "quality.basic",
            "suite_version": 1,
            "target_type": "agent",
            "target_id": "quality-agent",
            "target_revision_id": "quality-agent:v1",
            "cadence_seconds": 3600,
            "next_run_at": "2020-01-01T00:00:00+00:00",
            "execution_configuration": {
                "max_concurrency": 2,
                "case_timeout_seconds": 30,
            },
        },
        actor_id="quality-admin",
    )
    assert policy["enabled"] is True
    assert store.reconcile_due_eval_schedules() == 1
    refreshed = store.list_eval_schedule_policies()[0]
    eval_run_id = refreshed["last_eval_run_id"]
    assert eval_run_id
    assert store.get_eval_run(eval_run_id)["execution_job"]["status"] == "queued"
    assert store.reconcile_due_eval_schedules() == 0


def test_eval_api_scores_observations_and_persists_release_evidence(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "eval-api.db")
    client, headers = _admin_client(store)
    with client:
        suite = client.put(
            "/v1/admin/eval-suites/quality.basic/versions/1",
            headers=headers,
            json=_suite_body(),
        )
        assert suite.status_code == 200, suite.text
        run = client.post(
            "/v1/admin/eval-runs",
            headers=headers,
            json={
                "suite_id": "quality.basic",
                "suite_version": 1,
                "target_type": "agent",
                "target_id": "quality-agent",
                "target_revision_id": "quality-agent:v1",
                "idempotency_key": "quality-agent-v1-regression",
            },
        )
        assert run.status_code == 201, run.text
        eval_run_id = run.json()["eval_run_id"]
        observation = client.post(
            f"/v1/admin/eval-runs/{eval_run_id}/observations",
            headers=headers,
            json={
                "case_id": "answer",
                "output": {"answer": "yes"},
                "status": "completed",
                "latency_ms": 120,
                "cost_usd": 0.001,
            },
        )
        assert observation.status_code == 200, observation.text
        assert observation.json()["status"] == "passed"
        finalized = client.post(
            f"/v1/admin/eval-runs/{eval_run_id}/finalize", headers=headers
        )
        assert finalized.status_code == 200, finalized.text
        assert finalized.json()["status"] == "passed"
        assert finalized.json()["metrics"]["pass_rate"] == 1.0
        assert finalized.json()["metrics"]["total_cost_usd"] == 0.001
        assert finalized.json()["metrics"]["cost_coverage"] == 1.0
        assert finalized.json()["metrics"]["p95_latency_ms"] == 120
        store.save_release_gate_policy(
            value={
                "target_type": "agent",
                "target_id": "quality-agent",
                "target_revision_id": "quality-agent:v1",
                "required": True,
                "requirements": [
                    {
                        "suite_id": "quality.basic",
                        "suite_version": 1,
                        "min_pass_rate": 1,
                        "max_age_hours": 24,
                        "require_automated": True,
                    }
                ],
                "created_by": "quality-admin",
            }
        )
        manual_gate = store.evaluate_release_gate(
            target_type="agent",
            target_id="quality-agent",
            target_revision_id="quality-agent:v1",
            purpose="reject-manual-evidence-test",
            actor_id="quality-admin",
            decision_id="gate-reject-manual-evidence",
        )
        assert manual_gate["passed"] is False
        assert manual_gate["requirements"][0]["automated"] is False
        gate = client.put(
            "/v1/admin/release-gates/agent/quality-agent/quality-agent:v1",
            headers=headers,
            json={
                "required": True,
                "requirements": [
                    {
                        "suite_id": "quality.basic",
                        "suite_version": 1,
                        "min_pass_rate": 1.0,
                        "max_age_hours": 24,
                        "max_total_cost_usd": 0.002,
                        "max_p95_latency_ms": 250,
                        "min_cost_coverage": 1.0,
                    }
                ],
            },
        )
        assert gate.status_code == 200, gate.text
        listed = client.get("/v1/admin/eval-runs", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["items"][0]["results"][0]["score"] == 1.0


@pytest.mark.asyncio
async def test_agent_publication_is_blocked_until_exact_revision_passes_gate(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "eval-agent-gate.db")
    container = build_api_container(config=Config(), store=store)
    await container.evals.save_suite(_suite_body(), actor_id="quality-admin")
    await container.evals.save_release_gate(
        {
            "target_type": "agent",
            "target_id": "quality-agent",
            "target_revision_id": "quality-agent:v1",
            "required": True,
            "requirements": [
                {
                    "suite_id": "quality.basic",
                    "suite_version": 1,
                    "min_pass_rate": 1.0,
                    "max_age_hours": 24,
                }
            ],
        },
        actor_id="quality-admin",
    )
    definition = AgentDefinition(
        agent_id="quality-agent",
        name="Quality Agent",
        description="Must pass regression before publication",
        role="specialist",
    )
    revision = AgentRevision(
        revision_id="quality-agent:v1",
        agent_id="quality-agent",
        version=1,
        instructions="Return verified structured answers.",
        model_policy={"primary": "test/model"},
        created_by="quality-admin",
    )
    await container.platform.save_agent_revision(definition, revision)
    with pytest.raises(ConflictError, match="release gate failed"):
        await container.platform.publish_agent_revision(
            "quality-agent", "quality-agent:v1", actor_id="quality-admin"
        )

    eval_run = await container.evals.create_run(
        {
            "suite_id": "quality.basic",
            "suite_version": 1,
            "target_type": "agent",
            "target_id": "quality-agent",
            "target_revision_id": "quality-agent:v1",
            "idempotency_key": "publish-gate-pass",
        },
        actor_id="quality-admin",
    )
    await container.evals.record_observation(
        eval_run["eval_run_id"],
        {
            "case_id": "answer",
            "output": {"answer": "yes"},
            "status": "completed",
            "latency_ms": 100,
            "cost_usd": 0.001,
        },
    )
    finalized = await container.evals.finalize_run(eval_run["eval_run_id"])
    assert finalized["status"] == "passed"
    published = await container.platform.publish_agent_revision(
        "quality-agent",
        "quality-agent:v1",
        actor_id="quality-admin",
        rollout_policy={"require_healthy_workers": False},
    )
    assert published["revision"]["status"] == "published"
    with store._pool.connection() as connection:  # noqa: SLF001 - audit assertion
        decisions = connection.execute(
            """SELECT passed FROM release_gate_decisions
               WHERE target_type='agent' AND target_id='quality-agent'
               ORDER BY created_at"""
        ).fetchall()
    assert [bool(item["passed"]) for item in decisions] == [False, True]
    await container.close()


async def _pass_basic_eval(
    container, *, target_type: str, target_id: str, revision_id: str  # noqa: ANN001
) -> None:
    run = await container.evals.create_run(
        {
            "suite_id": "quality.basic",
            "suite_version": 1,
            "target_type": target_type,
            "target_id": target_id,
            "target_revision_id": revision_id,
        },
        actor_id="quality-admin",
    )
    await container.evals.record_observation(
        run["eval_run_id"],
        {
            "case_id": "answer",
            "output": {"answer": "yes"},
            "status": "completed",
            "latency_ms": 100,
            "cost_usd": 0.001,
        },
    )
    finalized = await container.evals.finalize_run(run["eval_run_id"])
    assert finalized["status"] == "passed"


@pytest.mark.asyncio
async def test_capability_and_scenario_publish_paths_use_the_same_gate(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "eval-other-gates.db")
    container = build_api_container(config=Config(), store=store)
    await container.evals.save_suite(_suite_body(), actor_id="quality-admin")
    targets = [
        ("capability", "quality.tool", "1.0.0"),
        ("scenario", "quality.scenario", "1"),
    ]
    for target_type, target_id, revision_id in targets:
        await container.evals.save_release_gate(
            {
                "target_type": target_type,
                "target_id": target_id,
                "target_revision_id": revision_id,
                "requirements": [
                    {
                        "suite_id": "quality.basic",
                        "suite_version": 1,
                        "min_pass_rate": 1.0,
                        "max_age_hours": 24,
                    }
                ],
            },
            actor_id="quality-admin",
        )

    capability = CapabilityDefinition(
        ref=CapabilityRef(
            "quality.tool",
            "1.0.0",
            CapabilityKind.TOOL,
            "test.quality",
            "1.0.0",
            "sha256:quality-tool",
        ),
        name="Quality tool",
        description="A gated capability",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        adapter="test.quality_tool",
    )
    scenario = ScenarioVersion(
        scenario_id="quality.scenario",
        version=1,
        name="Quality scenario",
        description="A gated scenario",
        fields=(),
        nodes=(),
        edges=(),
        allowed_capabilities=(),
        planning_mode="dynamic",
        execution_policy={},
        routing_rules=(),
    )
    store.save_scenario_version(scenario, actor_id="quality-admin")
    with pytest.raises(ConflictError, match="release gate failed"):
        await container.platform.publish_capability(capability, actor_id="quality-admin")
    with pytest.raises(ConflictError, match="release gate failed"):
        await container.scenarios.publish(
            "quality.scenario", 1, actor_id="quality-admin"
        )

    await _pass_basic_eval(
        container,
        target_type="capability",
        target_id="quality.tool",
        revision_id="1.0.0",
    )
    await _pass_basic_eval(
        container,
        target_type="scenario",
        target_id="quality.scenario",
        revision_id="1",
    )
    published_capability = await container.platform.publish_capability(
        capability,
        actor_id="quality-admin",
        rollout_policy={"require_healthy_workers": False},
    )
    published_scenario = await container.scenarios.publish(
        "quality.scenario",
        1,
        actor_id="quality-admin",
        rollout_policy={"require_healthy_workers": False},
    )
    assert published_capability["ref"]["version"] == "1.0.0"
    assert published_scenario["status"] == "published"
    await container.close()
