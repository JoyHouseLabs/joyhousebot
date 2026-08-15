"""The action view derives from Runtime state; it owns no separate Inbox."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.runtime.action_identity import payload_hash
from tests.support.postgres_store import PostgresTestStore


def _waiting_input(store: PostgresTestStore, run_id: str, *, user_id: str = "owner") -> None:
    store.create_runtime_run(
        run_id=run_id,
        user_id=user_id,
        session_id=f"session-{run_id}",
        agent_id="default",
        kind="agent",
        prompt="Need planning details",
        options={},
        initial_status="waiting_input",
    )
    store.create_input_request(
        input_request_id=f"input-{run_id}",
        run_id=run_id,
        user_id=user_id,
        scenario_id="scenario.plan",
        scenario_version=1,
        node_id="clarify",
        question="What is the deadline?",
        fields=[{"name": "deadline", "value_type": "string", "required": True}],
        source="scenario",
    )


def _waiting_approval(store: PostgresTestStore, run_id: str, *, user_id: str = "owner") -> None:
    store.create_runtime_run(
        run_id=run_id,
        user_id=user_id,
        session_id=f"session-{run_id}",
        agent_id="default",
        kind="agent",
        prompt="Publish the result",
        options={},
        initial_status="queued",
    )
    run = store.claim_runtime_run(run_id, worker_id="test-worker")
    assert run is not None
    payload = {"target": "https://example.test/post"}
    capability_ref = {
        "capability_id": "publish_result",
        "version": "1.0.0",
        "kind": "tool",
        "plugin_id": "test.publisher",
        "plugin_version": "1.0.0",
        "build_digest": "sha256:test",
    }
    store.create_runtime_turn(
        turn_id=f"turn-{run_id}",
        run_id=run_id,
        task_id=None,
        turn_index=0,
        model="test/default",
        request_hash="test-request",
        worker_id="test-worker",
    )
    action_id = f"action-{run_id}"
    store.create_action_intent(
        action_id=action_id,
        turn_id=f"turn-{run_id}",
        run_id=run_id,
        task_id=None,
        turn_index=0,
        action_index=0,
        capability_ref=capability_ref,
        input=payload,
        input_hash=payload_hash(payload),
        side_effect="external",
        idempotent=True,
        retryable=True,
        risk="high",
        approval_policy={"required": True, "required_role": "owner"},
        idempotency_key=f"action:{action_id}",
        invocation_id=f"invocation-{run_id}",
    )
    approval, _ = store.create_approval_request(
        approval_id=f"approval-{run_id}",
        run_id=run_id,
        action_id=action_id,
        user_id=user_id,
        capability_ref=capability_ref,
        input_hash=payload_hash(payload),
        input_preview={"target": "https://example.test/post"},
        risk="high",
        data_classification="internal",
        required_role="owner",
        requested_by="default",
        expires_in_seconds=3600,
    )
    assert store.suspend_run_for_approval(
        run_id=run_id,
        approval_id=approval.approval_id,
        action_id=action_id,
        worker_id="test-worker",
        lease_version=run.lease_version,
    )


def test_action_items_are_owner_scoped_and_derived_from_existing_runtime_state(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "action-items.db")
    store.create_api_access_token(user_id="owner", actor_id="test", token="owner-token")
    store.create_api_access_token(user_id="other", actor_id="test", token="other-token")
    _waiting_input(store, "run-input")
    _waiting_approval(store, "run-approval")
    _waiting_input(store, "run-foreign", user_id="other")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))

    with client:
        response = client.get("/v1/action-items", headers={"Authorization": "Bearer owner-token"})
        assert response.status_code == 200
        items = response.json()["items"]
        assert {item["kind"] for item in items} == {"input", "approval"}
        assert {item["run"]["run_id"] for item in items} == {"run-input", "run-approval"}
        input_item = next(item for item in items if item["kind"] == "input")
        assert input_item["input"]["question"] == "What is the deadline?"
        assert "run_options" not in input_item
        approval_item = next(item for item in items if item["kind"] == "approval")
        assert approval_item["approval"]["can_resolve"] is True
        assert approval_item["approval"]["capability_ref"]["capability_id"] == "publish_result"

        foreign = client.get("/v1/action-items", headers={"Authorization": "Bearer other-token"})
        assert foreign.status_code == 200
        assert [item["run"]["run_id"] for item in foreign.json()["items"]] == ["run-foreign"]
