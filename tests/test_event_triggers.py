from pathlib import Path

from fastapi.testclient import TestClient

from porthouse.api.app import create_app
from porthouse.bootstrap.container import build_api_container
from porthouse.config.schema import Config
from tests.support.postgres_store import PostgresTestStore


def _client(tmp_path: Path) -> tuple[TestClient, PostgresTestStore]:
    store = PostgresTestStore(tmp_path / "event-triggers.db")
    store.create_api_access_token(user_id="user-a", actor_id="test", token="token-a")
    store.create_api_access_token(user_id="user-b", actor_id="test", token="token-b")
    container = build_api_container(config=Config(), store=store)
    return TestClient(create_app(container)), store


def test_webhook_trigger_submits_idempotent_user_scoped_run(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    owner = {"Authorization": "Bearer token-a"}
    other = {"Authorization": "Bearer token-b"}
    with client:
        blank = client.post(
            "/v1/event-triggers",
            headers=owner,
            json={"name": "   ", "instruction": "   "},
        )
        assert blank.status_code == 422

        created = client.post(
            "/v1/event-triggers",
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
        assert trigger["endpoint_path"] == f"/v1/hooks/{trigger_id}"
        assert "secret_hash" not in trigger

        assert client.get("/v1/event-triggers", headers=other).json()["items"] == []
        listed = client.get("/v1/event-triggers", headers=owner).json()["items"]
        assert listed[0]["trigger_id"] == trigger_id
        assert "signing_secret" not in listed[0]

        event = {"event_type": "crm.contact.updated", "payload": {"contact_id": "42"}}
        wrong = client.post(
            f"/v1/hooks/{trigger_id}",
            headers={"X-Porthouse-Webhook-Secret": "wrong", "Idempotency-Key": "evt-1"},
            json=event,
        )
        assert wrong.status_code == 404
        missing_identity = client.post(
            f"/v1/hooks/{trigger_id}",
            headers={"X-Porthouse-Webhook-Secret": secret},
            json=event,
        )
        assert missing_identity.status_code == 422

        accepted = client.post(
            f"/v1/hooks/{trigger_id}",
            headers={
                "X-Porthouse-Webhook-Secret": secret,
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
            f"/v1/hooks/{trigger_id}",
            headers={
                "X-Porthouse-Webhook-Secret": secret,
                "Idempotency-Key": "evt-1",
            },
            json=event,
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["run_id"] == run_id
        conflict = client.post(
            f"/v1/hooks/{trigger_id}",
            headers={
                "X-Porthouse-Webhook-Secret": secret,
                "Idempotency-Key": "evt-1",
            },
            json={**event, "payload": {"contact_id": "99"}},
        )
        assert conflict.status_code == 409
        mismatch = client.post(
            f"/v1/hooks/{trigger_id}",
            headers={
                "X-Porthouse-Webhook-Secret": secret,
                "Idempotency-Key": "evt-2",
            },
            json={"event_type": "crm.deal.closed", "payload": {}},
        )
        assert mismatch.status_code == 422

        deliveries = client.get(
            f"/v1/event-trigger-deliveries?trigger_id={trigger_id}", headers=owner
        )
        assert deliveries.status_code == 200
        assert len(deliveries.json()["items"]) == 1
        assert deliveries.json()["items"][0]["run_id"] == run_id
        assert (
            client.get(
                f"/v1/event-trigger-deliveries?trigger_id={trigger_id}", headers=other
            ).status_code
            == 404
        )

        rotated = client.post(
            f"/v1/event-triggers/{trigger_id}/rotate-secret", headers=owner
        )
        assert rotated.status_code == 200
        new_secret = rotated.json()["signing_secret"]
        assert new_secret != secret
        assert (
            client.post(
                f"/v1/hooks/{trigger_id}",
                headers={
                    "X-Porthouse-Webhook-Secret": secret,
                    "Idempotency-Key": "evt-3",
                },
                json=event,
            ).status_code
            == 404
        )
        assert (
            client.patch(
                f"/v1/event-triggers/{trigger_id}",
                headers=other,
                json={"enabled": False},
            ).status_code
            == 404
        )
        disabled = client.patch(
            f"/v1/event-triggers/{trigger_id}",
            headers=owner,
            json={"enabled": False},
        )
        assert disabled.status_code == 200
        assert (
            client.post(
                f"/v1/hooks/{trigger_id}",
                headers={
                    "X-Porthouse-Webhook-Secret": new_secret,
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
            "/v1/schedules",
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
        submitted = client.post(f"/v1/schedules/{schedule_id}/runs", headers=owner)
        assert submitted.status_code == 202, submitted.text
        occurrence = submitted.json()
        assert occurrence["status"] == "submitted"
        run = store.get_runtime_run(occurrence["runId"])
        assert run is not None
        assert run.user_id == "user-a"
        assert run.options["metadata"]["schedule_id"] == schedule_id
        assert (
            client.post(
                f"/v1/schedules/{schedule_id}/runs",
                headers={"Authorization": "Bearer token-b"},
            ).status_code
            == 404
        )
