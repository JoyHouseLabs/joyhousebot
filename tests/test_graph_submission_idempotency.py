from pathlib import Path

from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from tests.support.postgres_store import PostgresTestStore


def test_graph_http_submission_is_idempotent_and_rejects_payload_reuse(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "graph-http-idempotency.db")
    store.create_api_access_token(
        user_id="graph-owner",
        actor_id="test",
        token="graph-owner-token",
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    headers = {
        "Authorization": "Bearer graph-owner-token",
        "Idempotency-Key": "product-publication-request-001",
    }
    value = {
        "goal": "publish a frozen Work",
        "session_id": "product-publication-001",
        "aggregate": False,
        "tasks": [{"id": "prepare", "prompt": "prepare"}],
    }

    with client:
        first = client.post("/v1/runs/graphs", headers=headers, json=value)
        replay = client.post("/v1/runs/graphs", headers=headers, json=value)
        conflict = client.post(
            "/v1/runs/graphs",
            headers=headers,
            json={**value, "goal": "a different publication"},
        )

    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    assert replay.json()["run_id"] == first.json()["run_id"]
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "conflict"
    assert len(store.list_runtime_runs(user_id="graph-owner", limit=10)) == 1
