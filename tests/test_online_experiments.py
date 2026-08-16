"""Stable online Agent-revision experiments remain outside business Apps."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from porthouse.api.app import create_app
from porthouse.bootstrap.container import build_api_container
from porthouse.config.schema import Config
from porthouse.domain.agents import AgentDefinition, AgentRevision
from porthouse.runtime.models import AgentEvent
from tests.support.postgres_store import PostgresTestStore


def _agents(store: PostgresTestStore) -> tuple[str, str, str]:
    definition = AgentDefinition(agent_id="experiment-agent", name="Experiment Agent")
    first = AgentRevision(
        revision_id="experiment-agent:v1",
        agent_id=definition.agent_id,
        version=1,
        status="published",
        model_policy={"primary": "test/model"},
    )
    second = AgentRevision(
        revision_id="experiment-agent:v2",
        agent_id=definition.agent_id,
        version=2,
        status="published",
        model_policy={"primary": "test/model"},
    )
    store.save_agent_revision(definition, first)
    store.save_agent_revision(definition, second)
    return definition.agent_id, first.revision_id, second.revision_id


def _experiment(agent_id: str, v1: str, v2: str) -> dict:
    return {
        "experiment_id": "experiment.prompt-policy-v2",
        "name": "Prompt policy V2",
        "description": "Compare two published Agent revisions with stable user assignment.",
        "target_type": "agent",
        "traffic_basis_points": 10_000,
        "variants": [
            {
                "variant_id": "control",
                "target_id": agent_id,
                "target_revision_id": v1,
                "weight_basis_points": 5_000,
            },
            {
                "variant_id": "candidate",
                "target_id": agent_id,
                "target_revision_id": v2,
                "weight_basis_points": 5_000,
            },
        ],
        "guardrails": {"min_assigned": 1, "max_failure_rate": 0.5},
    }


def test_experiment_assignment_is_stable_and_keeps_user_id_out_of_storage(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "online-experiment.db")
    agent_id, v1, v2 = _agents(store)
    created = store.save_experiment_draft(_experiment(agent_id, v1, v2), actor_id="admin")
    assert created["status"] == "draft"
    store.start_experiment(created["experiment_id"], actor_id="admin")
    first = store.select_experiment_variant(
        experiment_id=created["experiment_id"], subject_id="user-a", target_id=agent_id
    )
    second = store.select_experiment_variant(
        experiment_id=created["experiment_id"], subject_id="user-a", target_id=agent_id
    )
    assert first == second
    assert first and first["target_revision_id"] in {v1, v2}

    store.create_runtime_run(
        run_id="experiment-run",
        user_id="user-a",
        session_id="session-a",
        agent_id=agent_id,
        kind="agent",
        prompt="test",
        options={},
    )
    store.record_experiment_assignment(
        run_id="experiment-run", user_id="user-a", assignment=first
    )
    with store._pool.connection() as conn:  # test private-data invariant directly
        row = conn.execute(
            "SELECT subject_hash FROM runtime_experiment_assignments WHERE run_id='experiment-run'"
        ).fetchone()
    assert row and row["subject_hash"] != "user-a"

    claimed = store.claim_runtime_run("experiment-run", worker_id="worker", lease_seconds=30)
    assert claimed is not None
    assert store.finish_runtime_run(
        "experiment-run",
        status="failed",
        event=AgentEvent(
            run_id="experiment-run", type="run.failed", status="failed"
        ),
        error={"message": "intentional guardrail fixture"},
        worker_id="worker",
        lease_version=claimed.lease_version,
    ) is not None
    guardrail = store.enforce_experiment_guardrails(created["experiment_id"])
    assert guardrail["paused"] is True
    assert store.get_experiment(created["experiment_id"])["status"] == "paused"


def test_experiment_admin_api_can_start_and_report_summary(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "online-experiment-api.db")
    agent_id, v1, v2 = _agents(store)
    store.upsert_platform_admin(user_id="admin", permissions=["*"], actor_id="test")
    store.create_api_access_token(user_id="admin", actor_id="test", token="experiment-token")
    headers = {"Authorization": "Bearer experiment-token"}
    with TestClient(create_app(build_api_container(config=Config(), store=store))) as client:
        saved = client.put(
            "/v1/admin/experiments/experiment.prompt-policy-v2",
            headers=headers,
            json=_experiment(agent_id, v1, v2),
        )
        assert saved.status_code == 200, saved.text
        started = client.post(
            "/v1/admin/experiments/experiment.prompt-policy-v2/start", headers=headers
        )
        assert started.status_code == 200, started.text
        accepted = client.post(
            "/v1/runs",
            headers=headers,
            json={
                "execution": {"mode": "agent", "agent_id": agent_id},
                "input": {"content": "compare the policy"},
                "experiment_id": "experiment.prompt-policy-v2",
            },
        )
        assert accepted.status_code == 202, accepted.text
        run = store.get_runtime_run(accepted.json()["run_id"])
        assert run is not None
        assignment = dict(run.options["metadata"])["experiment_assignment"]
        assert assignment["target_revision_id"] in {v1, v2}
        summary = client.get(
            "/v1/admin/experiments/experiment.prompt-policy-v2/summary", headers=headers
        )
        assert summary.status_code == 200, summary.text
        assert summary.json()["summary"]["experiment"]["status"] == "running"
