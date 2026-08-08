"""Deterministic Eval evidence gates catalog publication."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.application.errors import ConflictError
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.domain.agents import AgentDefinition, AgentRevision
from joyhousebot.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
)
from joyhousebot.domain.scenarios import ScenarioVersion
from tests.support.postgres_store import PostgresTestStore


def _suite_body() -> dict:
    return {
        "suite_id": "quality.basic",
        "version": 1,
        "name": "Basic quality and budget gate",
        "target_types": ["agent", "scenario", "capability"],
        "thresholds": {"min_pass_rate": 1.0, "min_average_score": 1.0},
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
        "quality-agent", "quality-agent:v1", actor_id="quality-admin"
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
        capability, actor_id="quality-admin"
    )
    published_scenario = await container.scenarios.publish(
        "quality.scenario", 1, actor_id="quality-admin"
    )
    assert published_capability["ref"]["version"] == "1.0.0"
    assert published_scenario["status"] == "published"
    await container.close()
