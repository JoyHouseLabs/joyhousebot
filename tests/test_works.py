"""Works preserve Run results as versioned, governable, revocable outcomes."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from tests.support.postgres_store import PostgresTestStore


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _user(store: PostgresTestStore, user_id: str) -> dict[str, str]:
    token = f"token-{user_id}"
    store.create_api_access_token(user_id=user_id, actor_id="test", token=token)
    return _headers(token)


def _artifact(
    store: PostgresTestStore,
    *,
    user_id: str,
    run_id: str,
    artifact_id: str,
    content: object,
) -> None:
    store.create_runtime_run(
        run_id=run_id,
        user_id=user_id,
        session_id="works",
        agent_id="default",
        kind="agent",
        prompt="produce a work",
        options={},
    )
    store.add_runtime_artifact(
        artifact_id=artifact_id,
        run_id=run_id,
        name="result",
        media_type="application/json",
        content=content,
    )


def test_work_versions_publication_sharing_revocation_and_audit(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "works-lifecycle.db")
    owner = _user(store, "work-owner")
    collaborator = _user(store, "work-editor")
    stranger = _user(store, "work-stranger")
    _artifact(
        store,
        user_id="work-owner",
        run_id="work-run-v1",
        artifact_id="work-artifact-v1",
        content={"headline": "First published result", "evidence": [1, 2]},
    )
    _artifact(
        store,
        user_id="work-editor",
        run_id="work-run-v2",
        artifact_id="work-artifact-v2",
        content={"headline": "Second published result", "evidence": [1, 2, 3]},
    )
    _artifact(
        store,
        user_id="work-stranger",
        run_id="foreign-run",
        artifact_id="foreign-artifact",
        content={"private": True},
    )
    container = build_api_container(config=Config(), store=store)
    with TestClient(create_app(container)) as client:
        foreign = client.post(
            "/v1/works",
            headers=owner,
            json={
                "run_id": "foreign-run",
                "artifact_id": "foreign-artifact",
                "title": "Must not leak",
            },
        )
        assert foreign.status_code == 404

        created = client.post(
            "/v1/works",
            headers={**owner, "Idempotency-Key": "first-work"},
            json={
                "run_id": "work-run-v1",
                "artifact_id": "work-artifact-v1",
                "title": "Evidence-backed report",
                "description": "A durable outcome, not a transient answer.",
            },
        )
        assert created.status_code == 201, created.text
        work = created.json()
        work_id = work["work_id"]
        slug = work["public_slug"]
        assert work["current_version"] == 1
        assert work["published_version"] is None

        unsafe_publish = client.patch(
            f"/v1/works/{work_id}",
            headers=owner,
            json={"status": "published", "visibility": "public"},
        )
        assert unsafe_publish.status_code == 422
        assert "classification" in unsafe_publish.text

        published = client.patch(
            f"/v1/works/{work_id}",
            headers=owner,
            json={
                "status": "published",
                "visibility": "public",
                "data_classification": "public",
            },
        )
        assert published.status_code == 200, published.text
        assert published.json()["published_version"] == 1
        public_v1 = client.get(f"/v1/public/works/{slug}")
        assert public_v1.status_code == 200
        assert public_v1.json()["version"] == 1
        assert "owner_user_id" not in public_v1.json()
        assert "source_run_id" not in public_v1.json()

        shared = client.post(
            f"/v1/works/{work_id}/shares",
            headers=owner,
            json={"permission": "download", "expires_in_seconds": 3600},
        )
        assert shared.status_code == 201, shared.text
        token = shared.json()["token"]
        share_id = shared.json()["share_id"]
        assert client.get(f"/v1/public/shares/{token}").json()["permission"] == "download"
        listed_shares = client.get(f"/v1/works/{work_id}/shares", headers=owner)
        assert token not in listed_shares.text
        with store._pool.connection() as conn:
            persisted = conn.execute(
                "SELECT token_hash FROM work_shares WHERE share_id=%s", (share_id,)
            ).fetchone()
        assert persisted is not None
        assert persisted["token_hash"] != token

        granted = client.put(
            f"/v1/works/{work_id}/collaborators/work-editor",
            headers=owner,
            json={"role": "editor"},
        )
        assert granted.status_code == 200, granted.text
        new_version = client.post(
            f"/v1/works/{work_id}/versions",
            headers=collaborator,
            json={
                "run_id": "work-run-v2",
                "artifact_id": "work-artifact-v2",
                "change_note": "Expanded evidence",
            },
        )
        assert new_version.status_code == 201, new_version.text
        assert new_version.json()["current_version"] == 2
        assert new_version.json()["published_version"] == 1
        assert new_version.json()["status"] == "draft"

        # A new draft does not destroy the prior published outcome or pinned share.
        assert client.get(f"/v1/public/works/{slug}").json()["version"] == 1
        assert client.get(f"/v1/public/shares/{token}").json()["version"] == 1
        forbidden_publish = client.patch(
            f"/v1/works/{work_id}",
            headers=collaborator,
            json={"status": "published"},
        )
        assert forbidden_publish.status_code == 404

        republished = client.patch(
            f"/v1/works/{work_id}",
            headers=owner,
            json={"status": "published"},
        )
        assert republished.status_code == 200
        assert republished.json()["published_version"] == 2
        assert client.get(f"/v1/public/works/{slug}").json()["version"] == 2
        assert client.get(f"/v1/public/shares/{token}").json()["version"] == 1

        revoked = client.post(
            f"/v1/works/{work_id}/shares/{share_id}/revoke", headers=owner
        )
        assert revoked.status_code == 200
        assert client.get(f"/v1/public/shares/{token}").status_code == 404

        collaborators = client.get(
            f"/v1/works/{work_id}/collaborators", headers=owner
        )
        assert collaborators.json()["items"][0]["user_id"] == "work-editor"
        removed = client.delete(
            f"/v1/works/{work_id}/collaborators/work-editor", headers=owner
        )
        assert removed.status_code == 204
        assert client.get(f"/v1/works/{work_id}", headers=collaborator).status_code == 404
        assert client.get(f"/v1/works/{work_id}", headers=stranger).status_code == 404

        audit = client.get(f"/v1/works/{work_id}/audit", headers=owner)
        event_types = {item["event_type"] for item in audit.json()["items"]}
        assert {
            "work.created",
            "work.published",
            "work.version_created",
            "share.created",
            "share.accessed",
            "share.revoked",
            "collaborator.granted",
            "collaborator.revoked",
        } <= event_types


def test_archived_work_is_not_public_and_cannot_be_reopened(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "works-archive.db")
    owner = _user(store, "archive-owner")
    _artifact(
        store,
        user_id="archive-owner",
        run_id="archive-run",
        artifact_id="archive-artifact",
        content="finished work",
    )
    container = build_api_container(config=Config(), store=store)
    with TestClient(create_app(container)) as client:
        created = client.post(
            "/v1/works",
            headers=owner,
            json={
                "run_id": "archive-run",
                "artifact_id": "archive-artifact",
                "title": "Archived work",
                "data_classification": "public",
            },
        ).json()
        work_id = created["work_id"]
        slug = created["public_slug"]
        assert client.patch(
            f"/v1/works/{work_id}",
            headers=owner,
            json={"status": "published", "visibility": "public"},
        ).status_code == 200
        assert client.patch(
            f"/v1/works/{work_id}", headers=owner, json={"status": "archived"}
        ).status_code == 200
        assert client.get(f"/v1/public/works/{slug}").status_code == 404
        assert client.patch(
            f"/v1/works/{work_id}", headers=owner, json={"status": "published"}
        ).status_code == 422
