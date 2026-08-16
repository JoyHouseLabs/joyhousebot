"""Owner-scoped revocation contracts for private Runtime Input Assets."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from porthouse.api.app import create_app
from porthouse.bootstrap.container import build_api_container
from porthouse.config.schema import Config
from tests.support.postgres_store import PostgresTestStore


def _upload(client: TestClient, *, token: str, key: str, body: bytes) -> dict[str, object]:
    response = client.post(
        "/v1/input-assets?file_name=resume.pdf",
        content=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": key,
            "X-Content-SHA256": hashlib.sha256(body).hexdigest(),
            "Content-Type": "application/pdf",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_input_asset_delete_is_owner_scoped_idempotent_and_audited(tmp_path: Path) -> None:
    store = PostgresTestStore(
        tmp_path / "input-asset-delete.db",
        input_asset_directory=str(tmp_path / "input-objects"),
    )
    store.create_api_access_token(user_id="owner-a", actor_id="test", token="owner-a-token")
    store.create_api_access_token(user_id="owner-b", actor_id="test", token="owner-b-token")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner = {"Authorization": "Bearer owner-a-token"}
    foreign = {"Authorization": "Bearer owner-b-token"}

    with client:
        asset = _upload(client, token="owner-a-token", key="delete-a", body=b"%PDF-a")
        asset_id = str(asset["asset_id"])
        denied = client.delete(f"/v1/input-assets/{asset_id}", headers=foreign)
        deleted = client.delete(f"/v1/input-assets/{asset_id}", headers=owner)
        repeated = client.delete(f"/v1/input-assets/{asset_id}", headers=owner)
        hidden = client.get(f"/v1/input-assets/{asset_id}", headers=owner)

    assert denied.status_code == 404
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert deleted.json()["deleted_at"]
    assert repeated.json() == deleted.json()
    assert hidden.status_code == 404
    with store._pool.connection() as connection:
        events = connection.execute(
            """SELECT event_type,data FROM runtime_input_asset_events
               WHERE asset_id=%s ORDER BY event_id""",
            (asset_id,),
        ).fetchall()
    assert [str(event["event_type"]) for event in events] == ["created", "deleted"]
    assert str(events[-1]["data"]["actor_id"]).startswith("token:")


def test_input_asset_delete_rejects_assets_bound_to_active_runs(tmp_path: Path) -> None:
    store = PostgresTestStore(
        tmp_path / "input-asset-active.db",
        input_asset_directory=str(tmp_path / "active-input-objects"),
    )
    store.create_api_access_token(user_id="owner-a", actor_id="test", token="owner-a-token")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))

    with client:
        asset = _upload(client, token="owner-a-token", key="active-a", body=b"%PDF-active")
        store.create_runtime_run(
            run_id="run-active-input",
            user_id="owner-a",
            session_id="active-input",
            agent_id="default",
            kind="agent",
            prompt="consume input",
            options={},
            input_asset_ids=[str(asset["asset_id"])],
        )
        response = client.delete(
            f"/v1/input-assets/{asset['asset_id']}",
            headers={"Authorization": "Bearer owner-a-token"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "Input Asset is still bound to an active Run"
