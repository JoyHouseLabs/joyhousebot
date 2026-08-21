from pathlib import Path

from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from tests.support.postgres_store import PostgresTestStore


def _client(tmp_path: Path) -> tuple[TestClient, PostgresTestStore]:
    store = PostgresTestStore(tmp_path / "event-triggers.db")
    store.create_operator_access_token(user_id="user-a", actor_id="test", token="token-a")
    store.create_operator_access_token(user_id="user-b", actor_id="test", token="token-b")
    container = build_api_container(config=Config(), store=store)
    return TestClient(create_app(container)), store


def test_webhook_trigger_submits_idempotent_user_scoped_run(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    owner = {"Authorization": "Bearer token-a"}
    other = {"Authorization": "Bearer token-b"}
    with client:
        blank = client.post(
            "/control/v1/event-triggers",
            headers=owner,
            json={"name": "   ", "instruction": "   "},
        )
        assert blank.status_code == 422

        created = client.post(
            "/control/v1/event-triggers",
            headers=owner,
            json={
                "name": "CRM contact changed",
                "agent_id": "default",
                "event_type_filter": "crm.contact.updated",
                "instruction": "Review this contact update and identify the next action.",
                "session_mode": "per_event",
            },
        )
        assert created.status_code == 201, created.text
        trigger = created.json()
        trigger_id = trigger["trigger_id"]
        secret = trigger["signing_secret"]
        assert trigger["endpoint_path"] == f"/events/v1/hooks/{trigger_id}"
        assert "secret_hash" not in trigger

        assert client.get("/control/v1/event-triggers", headers=other).json()["items"] == []
        listed = client.get("/control/v1/event-triggers", headers=owner).json()["items"]
        assert listed[0]["trigger_id"] == trigger_id
        assert "signing_secret" not in listed[0]

        event = {"event_type": "crm.contact.updated", "payload": {"contact_id": "42"}}
        wrong = client.post(
            f"/events/v1/hooks/{trigger_id}",
            headers={"X-JoyHouseBot-Webhook-Secret": "wrong", "Idempotency-Key": "evt-1"},
            json=event,
        )
        assert wrong.status_code == 404
        missing_identity = client.post(
            f"/events/v1/hooks/{trigger_id}",
            headers={"X-JoyHouseBot-Webhook-Secret": secret},
            json=event,
        )
        assert missing_identity.status_code == 422

        accepted = client.post(
            f"/events/v1/hooks/{trigger_id}",
            headers={
                "X-JoyHouseBot-Webhook-Secret": secret,
                "Idempotency-Key": "evt-1",
            },
            json=event,
        )
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["duplicate"] is False
        run_id = accepted.json()["run_id"]
        run = store.get_runtime_run(run_id)
        assert run is not None
        assert run.user_id == "user-a"
        assert run.options["channel"] == "webhook"
        assert run.options["metadata"]["event_trigger_id"] == trigger_id

        duplicate = client.post(
            f"/events/v1/hooks/{trigger_id}",
            headers={
                "X-JoyHouseBot-Webhook-Secret": secret,
                "Idempotency-Key": "evt-1",
            },
            json=event,
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["run_id"] == run_id
        conflict = client.post(
            f"/events/v1/hooks/{trigger_id}",
            headers={
                "X-JoyHouseBot-Webhook-Secret": secret,
                "Idempotency-Key": "evt-1",
            },
            json={**event, "payload": {"contact_id": "99"}},
        )
        assert conflict.status_code == 409
        mismatch = client.post(
            f"/events/v1/hooks/{trigger_id}",
            headers={
                "X-JoyHouseBot-Webhook-Secret": secret,
                "Idempotency-Key": "evt-2",
            },
            json={"event_type": "crm.deal.closed", "payload": {}},
        )
        assert mismatch.status_code == 422

        deliveries = client.get(
            f"/control/v1/event-trigger-deliveries?trigger_id={trigger_id}", headers=owner
        )
        assert deliveries.status_code == 200
        assert len(deliveries.json()["items"]) == 1
        assert deliveries.json()["items"][0]["run_id"] == run_id
        assert (
            client.get(
                f"/control/v1/event-trigger-deliveries?trigger_id={trigger_id}", headers=other
            ).status_code
            == 404
        )

        rotated = client.post(
            f"/control/v1/event-triggers/{trigger_id}/rotate-secret", headers=owner
        )
        assert rotated.status_code == 200
        new_secret = rotated.json()["signing_secret"]
        assert new_secret != secret
        assert (
            client.post(
                f"/events/v1/hooks/{trigger_id}",
                headers={
                    "X-JoyHouseBot-Webhook-Secret": secret,
                    "Idempotency-Key": "evt-3",
                },
                json=event,
            ).status_code
            == 404
        )
        assert (
            client.patch(
                f"/control/v1/event-triggers/{trigger_id}",
                headers=other,
                json={"enabled": False},
            ).status_code
            == 404
        )
        disabled = client.patch(
            f"/control/v1/event-triggers/{trigger_id}",
            headers=owner,
            json={"enabled": False},
        )
        assert disabled.status_code == 200
        assert (
            client.post(
                f"/events/v1/hooks/{trigger_id}",
                headers={
                    "X-JoyHouseBot-Webhook-Secret": new_secret,
                    "Idempotency-Key": "evt-3",
                },
                json=event,
            ).status_code
            == 409
        )


def test_schedule_manual_run_is_a_real_runtime_submission(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    owner = {"Authorization": "Bearer token-a"}
    with client:
        created = client.post(
            "/control/v1/schedules",
            headers=owner,
            json={
                "name": "daily review",
                "agent_id": "default",
                "schedule": {"kind": "every", "every_ms": 3_600_000},
                "payload": {"message": "Review today's progress."},
            },
        )
        assert created.status_code == 201
        schedule_id = created.json()["id"]
        submitted = client.post(f"/control/v1/schedules/{schedule_id}/runs", headers=owner)
        assert submitted.status_code == 202, submitted.text
        occurrence = submitted.json()
        assert occurrence["status"] == "submitted"
        run = store.get_runtime_run(occurrence["runId"])
        assert run is not None
        assert run.user_id == "user-a"
        assert run.options["metadata"]["schedule_id"] == schedule_id
        assert (
            client.post(
                f"/control/v1/schedules/{schedule_id}/runs",
                headers={"Authorization": "Bearer token-b"},
            ).status_code
            == 404
        )


def test_workflow_trigger_requires_workflow_id_and_defaults_to_run(tmp_path: Path) -> None:
    client, _store = _client(tmp_path)
    owner = {"Authorization": "Bearer token-a"}
    with client:
        invalid = client.post(
            "/control/v1/event-triggers",
            headers=owner,
            json={
                "name": "missing workflow",
                "instruction": "refresh",
                "action": "workflow",
            },
        )
        assert invalid.status_code == 422
        assert "workflow_id" in invalid.text
        created = client.post(
            "/control/v1/event-triggers",
            headers=owner,
            json={"name": "legacy", "instruction": "review"},
        )
        assert created.status_code == 201
        assert created.json()["action"] == "run"
        assert created.json()["workflow_id"] is None


def test_workflow_trigger_delivers_a_graph_run_with_same_dispatch_core(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "workflow-trigger.db")
    store.create_operator_access_token(user_id="user-a", actor_id="test", token="token-a")
    container = build_api_container(config=Config(), store=store)
    client = TestClient(create_app(container))
    owner = {"Authorization": "Bearer token-a"}
    with client:
        workflow = client.post(
            "/control/v1/workflows",
            headers=owner,
            json={
                "name": "panel refresh agent",
                "goal": "refresh the panel",
                "graph": {
                    "name": "panel refresh agent",
                    "summary": "agent step",
                    "risk_level": "low",
                    "estimated_duration_minutes": 1,
                    "nodes": [
                        {
                            "id": "collect",
                            "name": "collect",
                            "objective": "collect the payload",
                            "kind": "agent",
                            "agent_id": "default",
                            "dependencies": [],
                            "allowed_tools": [],
                            "skills": [],
                            "max_attempts": 1,
                        }
                    ],
                    "policies": {"max_concurrent": 1, "fail_fast": True, "aggregate": True},
                },
            },
        )
        assert workflow.status_code == 201, workflow.text
        workflow_id = workflow.json()["workflow_id"]
        revision_id = workflow.json()["current_revision_id"]
        published = client.post(
            f"/control/v1/workflows/{workflow_id}/publish",
            headers=owner,
            json={"revision_id": revision_id},
        )
        assert published.status_code == 200, published.text
        created = client.post(
            "/control/v1/event-triggers",
            headers=owner,
            json={
                "name": "panel webhook",
                "instruction": "refresh the panel",
                "action": "workflow",
                "workflow_id": workflow_id,
                "session_mode": "per_event",
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["action"] == "workflow"
        trigger_id = created.json()["trigger_id"]
        secret = created.json()["signing_secret"]
        accepted = client.post(
            f"/events/v1/hooks/{trigger_id}",
            headers={
                "X-JoyHouseBot-Webhook-Secret": secret,
                "Idempotency-Key": "wf-evt-1",
            },
            json={"event_type": "panel.refresh", "payload": {"topic": "llm"}},
        )
        assert accepted.status_code == 202, accepted.text
        run_id = accepted.json()["run_id"]
        run = store.get_runtime_run(run_id)
        assert run is not None
        assert run.kind == "graph"
        assert run.options["metadata"]["event_trigger_id"] == trigger_id
        assert run.options["metadata"]["workflow_id"] == workflow_id
        duplicate = client.post(
            f"/events/v1/hooks/{trigger_id}",
            headers={
                "X-JoyHouseBot-Webhook-Secret": secret,
                "Idempotency-Key": "wf-evt-1",
            },
            json={"event_type": "panel.refresh", "payload": {"topic": "llm"}},
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["run_id"] == run_id


def test_workflow_trigger_update_switches_action_and_clears_stale_fields(
    tmp_path: Path,
) -> None:
    client, _store = _client(tmp_path)
    owner = {"Authorization": "Bearer token-a"}
    with client:
        created = client.post(
            "/control/v1/event-triggers",
            headers=owner,
            json={"name": "switchable", "instruction": "review"},
        )
        assert created.status_code == 201
        trigger_id = created.json()["trigger_id"]
        switched = client.patch(
            f"/control/v1/event-triggers/{trigger_id}",
            headers=owner,
            json={"action": "workflow", "workflow_id": "wf_abc123"},
        )
        assert switched.status_code == 200, switched.text
        assert switched.json()["action"] == "workflow"
        assert switched.json()["workflow_id"] == "wf_abc123"
        reverted = client.patch(
            f"/control/v1/event-triggers/{trigger_id}",
            headers=owner,
            json={"action": "run"},
        )
        assert reverted.status_code == 200
        assert reverted.json()["action"] == "run"
        assert reverted.json()["workflow_id"] is None
