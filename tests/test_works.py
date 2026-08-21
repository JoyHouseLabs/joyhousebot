"""Works preserve Run results as versioned, governable, revocable outcomes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.application.app_releases import AppReleaseService
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from tests.support.postgres_store import PostgresTestStore


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _user(store: PostgresTestStore, user_id: str) -> dict[str, str]:
    token = f"token-{user_id}"
    store.create_operator_access_token(user_id=user_id, actor_id="test", token=token)
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


def _consumer_manifest() -> dict:
    return {
        "schema_version": 1,
        "app_id": "app.content-studio",
        "version": "1.0.0",
        "name": "Content Studio",
        "description": "Turns approved research into a content production plan.",
        "publisher": "joyhousebot",
        "core": {"min_version": "2.0.0"},
        "extensions": [],
        "capabilities": [],
        "assets": {"agents": [], "teams": [], "skills": [], "workflows": [], "scenarios": []},
        "connections": [],
        "permissions": ["work_handoffs.read", "work_handoffs.write"],
        "secrets": [],
        "work_consumers": [
            {
                "consumer_id": "content-plan",
                "name": "内容生产计划",
                "description": "将资料包转化为待确认的内容生产计划。",
                "purposes": ["create_content_plan"],
                "media_types": ["application/json"],
                "max_data_classification": "internal",
                "input_schema": {"type": "object"},
            }
        ],
    }


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
            "/control/v1/works",
            headers=owner,
            json={
                "run_id": "foreign-run",
                "artifact_id": "foreign-artifact",
                "title": "Must not leak",
            },
        )
        assert foreign.status_code == 404

        created = client.post(
            "/control/v1/works",
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
        assert work["version"]["source_artifact_sha256"]
        assert work["version"]["evidence_manifest_sha256"]
        assert work["version"]["evidence_manifest"]["artifact"]["artifact_id"] == "work-artifact-v1"

        unsafe_publish = client.patch(
            f"/control/v1/works/{work_id}",
            headers=owner,
            json={"status": "published", "visibility": "public"},
        )
        assert unsafe_publish.status_code == 422
        assert "classification" in unsafe_publish.text

        published = client.patch(
            f"/control/v1/works/{work_id}",
            headers=owner,
            json={
                "status": "published",
                "visibility": "public",
                "data_classification": "public",
            },
        )
        assert published.status_code == 200, published.text
        assert published.json()["published_version"] == 1
        public_v1 = client.get(f"/shares/v1/works/{slug}")
        assert public_v1.status_code == 200
        assert public_v1.json()["version"] == 1
        assert "evidence_manifest" not in public_v1.json()
        assert "owner_user_id" not in public_v1.json()
        assert "source_run_id" not in public_v1.json()

        shared = client.post(
            f"/control/v1/works/{work_id}/shares",
            headers=owner,
            json={"permission": "download", "expires_in_seconds": 3600},
        )
        assert shared.status_code == 201, shared.text
        token = shared.json()["token"]
        share_id = shared.json()["share_id"]
        assert client.get(f"/shares/v1/tokens/{token}").json()["permission"] == "download"
        listed_shares = client.get(f"/control/v1/works/{work_id}/shares", headers=owner)
        assert token not in listed_shares.text
        with store._pool.connection() as conn:
            persisted = conn.execute(
                "SELECT token_hash FROM work_shares WHERE share_id=%s", (share_id,)
            ).fetchone()
        assert persisted is not None
        assert persisted["token_hash"] != token

        granted = client.put(
            f"/control/v1/works/{work_id}/collaborators/work-editor",
            headers=owner,
            json={"role": "editor"},
        )
        assert granted.status_code == 200, granted.text
        new_version = client.post(
            f"/control/v1/works/{work_id}/versions",
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
        assert client.get(f"/shares/v1/works/{slug}").json()["version"] == 1
        assert client.get(f"/shares/v1/tokens/{token}").json()["version"] == 1
        forbidden_publish = client.patch(
            f"/control/v1/works/{work_id}",
            headers=collaborator,
            json={"status": "published"},
        )
        assert forbidden_publish.status_code == 404

        republished = client.patch(
            f"/control/v1/works/{work_id}",
            headers=owner,
            json={"status": "published"},
        )
        assert republished.status_code == 200
        assert republished.json()["published_version"] == 2
        assert client.get(f"/shares/v1/works/{slug}").json()["version"] == 2
        assert client.get(f"/shares/v1/tokens/{token}").json()["version"] == 1

        revoked = client.post(
            f"/control/v1/works/{work_id}/shares/{share_id}/revoke", headers=owner
        )
        assert revoked.status_code == 200
        assert client.get(f"/shares/v1/tokens/{token}").status_code == 404

        collaborators = client.get(f"/control/v1/works/{work_id}/collaborators", headers=owner)
        assert collaborators.json()["items"][0]["user_id"] == "work-editor"
        removed = client.delete(
            f"/control/v1/works/{work_id}/collaborators/work-editor", headers=owner
        )
        assert removed.status_code == 204
        assert client.get(f"/control/v1/works/{work_id}", headers=collaborator).status_code == 404
        assert client.get(f"/control/v1/works/{work_id}", headers=stranger).status_code == 404

        audit = client.get(f"/control/v1/works/{work_id}/audit", headers=owner)
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


def test_uri_artifact_requires_content_digest_and_object_version_for_work(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "work-uri-integrity.db")
    owner = _user(store, "work-uri-owner")
    store.create_runtime_run(
        run_id="work-uri-run",
        user_id="work-uri-owner",
        session_id="works",
        agent_id="default",
        kind="agent",
        prompt="produce object",
        options={},
    )
    store.add_runtime_artifact(
        artifact_id="work-uri-artifact",
        run_id="work-uri-run",
        name="external-object",
        media_type="application/pdf",
        uri="https://objects.example/report.pdf",
    )
    container = build_api_container(config=Config(), store=store)
    with TestClient(create_app(container)) as client:
        response = client.post(
            "/control/v1/works",
            headers=owner,
            json={
                "run_id": "work-uri-run",
                "artifact_id": "work-uri-artifact",
                "title": "Unfrozen URI",
            },
        )
    assert response.status_code == 422
    assert "object_version" in response.text


@pytest.mark.asyncio
async def test_work_handoff_pins_a_version_and_records_app_receipts(tmp_path: Path) -> None:
    store = PostgresTestStore(tmp_path / "work-handoff.db")
    app_releases = AppReleaseService(store)
    await app_releases.save_draft(_consumer_manifest(), actor_id="admin")
    await app_releases.publish(
        "app.content-studio", "1.0.0", actor_id="admin", user_id="handoff-owner"
    )
    installation = await app_releases.install(
        "app.content-studio",
        "1.0.0",
        user_id="handoff-owner",
        actor_id="admin",
        configuration={},
        granted_permissions=["work_handoffs.read", "work_handoffs.write"],
    )
    installation = await app_releases.transition(
        installation["installation_id"],
        user_id="handoff-owner",
        actor_id="admin",
        action="activate",
    )
    owner = _user(store, "handoff-owner")
    _artifact(
        store,
        user_id="handoff-owner",
        run_id="handoff-run",
        artifact_id="handoff-artifact",
        content={"opportunity": "Create an AI writing workshop", "sources": ["meeting"]},
    )
    container = build_api_container(config=Config(), store=store)
    with TestClient(create_app(container)) as client:
        work = client.post(
            "/control/v1/works",
            headers={**owner, "Idempotency-Key": "handoff-work"},
            json={
                "run_id": "handoff-run",
                "artifact_id": "handoff-artifact",
                "title": "Workshop opportunity",
            },
        )
        assert work.status_code == 201, work.text
        work_id = work.json()["work_id"]

        consumers = client.get(f"/control/v1/works/{work_id}/consumers", headers=owner)
        assert consumers.status_code == 200, consumers.text
        assert consumers.json()["items"] == [
            {
                "installation_id": installation["installation_id"],
                "app_id": "app.content-studio",
                "app_version": "1.0.0",
                "app_name": "Content Studio",
                "consumer_id": "content-plan",
                "name": "内容生产计划",
                "description": "将资料包转化为待确认的内容生产计划。",
                "purposes": ["create_content_plan"],
                "media_types": ["application/json"],
                "input_schema": {"type": "object"},
            }
        ]
        payload = {
            "installation_id": installation["installation_id"],
            "consumer_id": "content-plan",
            "purpose": "create_content_plan",
        }
        handoff = client.post(
            f"/control/v1/works/{work_id}/handoffs",
            headers={**owner, "Idempotency-Key": "handoff-once"},
            json=payload,
        )
        assert handoff.status_code == 201, handoff.text
        handoff_value = handoff.json()
        assert handoff_value["status"] == "authorized"
        assert handoff_value["work_version"] == 1
        duplicate = client.post(
            f"/control/v1/works/{work_id}/handoffs",
            headers={**owner, "Idempotency-Key": "handoff-once"},
            json=payload,
        )
        assert duplicate.status_code == 201
        assert duplicate.json()["handoff_id"] == handoff_value["handoff_id"]

        app_client, app_secret = store.create_app_client(
            app_id="app.content-studio",
            name="Content Studio service",
            allowed_scopes=["work_handoffs.read", "work_handoffs.write"],
            actor_id="admin",
        )
        grant = store.create_app_delegation_grant(
            client_id=app_client["client_id"],
            installation_id=installation["installation_id"],
            user_id="handoff-owner",
            scopes=["work_handoffs.read", "work_handoffs.write"],
            expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            actor_id="handoff-owner",
        )
        issued = store.issue_app_delegated_token(
            client_id=app_client["client_id"],
            client_secret=app_secret,
            grant_id=grant["grant_id"],
            requested_scopes=["work_handoffs.read", "work_handoffs.write"],
            ttl_seconds=900,
        )
        assert issued is not None
        _, access_token = issued
        app_headers = {"Authorization": f"Bearer {access_token}"}
        assert client.get("/control/v1/works", headers=app_headers).status_code == 403
        assert (
            client.get(f"/control/v1/works/{work_id}/handoffs", headers=app_headers).status_code
            == 403
        )
        assert (
            client.post(
                f"/handoffs/v1/{handoff_value['handoff_id']}/receipt",
                headers={**owner, "Idempotency-Key": "owner-cannot-receipt"},
                json={"status": "accepted"},
            ).status_code
            == 422
        )
        frozen_input = client.get(
            f"/handoffs/v1/{handoff_value['handoff_id']}/input", headers=app_headers
        )
        assert frozen_input.status_code == 200, frozen_input.text
        assert frozen_input.json()["work"]["version"]["content"] == {
            "opportunity": "Create an AI writing workshop",
            "sources": ["meeting"],
        }

        accepted = client.post(
            f"/handoffs/v1/{handoff_value['handoff_id']}/receipt",
            headers={**app_headers, "Idempotency-Key": "handoff-receipt"},
            json={
                "status": "accepted",
                "external_reference": "content-plan-42",
                "summary": "Content Studio accepted the source material.",
            },
        )
        assert accepted.status_code == 201, accepted.text
        receipt = client.post(
            f"/handoffs/v1/{handoff_value['handoff_id']}/receipt",
            headers={**app_headers, "Idempotency-Key": "handoff-verified"},
            json={
                "status": "verified",
                "external_reference": "content-plan-42",
                "summary": "Created a reviewed content plan.",
            },
        )
        assert receipt.status_code == 201, receipt.text
        assert receipt.json()["handoff"]["status"] == "verified"
        assert receipt.json()["receipt"]["external_reference"] == "content-plan-42"
        receipts = client.get(
            f"/handoffs/v1/{handoff_value['handoff_id']}/receipts", headers=owner
        )
        assert receipts.status_code == 200
        assert receipts.json()["items"][0]["status"] == "verified"
        app_receipts = client.get(
            f"/handoffs/v1/{handoff_value['handoff_id']}/receipts", headers=app_headers
        )
        assert app_receipts.status_code == 200
        assert app_receipts.json()["items"][0]["status"] == "verified"
        assert (
            client.get(
                f"/handoffs/v1/{handoff_value['handoff_id']}/input",
                headers=app_headers,
            ).status_code
            == 404
        )
        audit = client.get(f"/control/v1/works/{work_id}/audit", headers=owner)
        assert {item["event_type"] for item in audit.json()["items"]} >= {
            "handoff.authorized",
            "handoff.input_accessed",
            "handoff.verified",
        }
    store.close()


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
            "/control/v1/works",
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
        assert (
            client.patch(
                f"/control/v1/works/{work_id}",
                headers=owner,
                json={"status": "published", "visibility": "public"},
            ).status_code
            == 200
        )
        assert (
            client.patch(
                f"/control/v1/works/{work_id}", headers=owner, json={"status": "archived"}
            ).status_code
            == 200
        )
        assert client.get(f"/shares/v1/works/{slug}").status_code == 404
        assert (
            client.patch(
                f"/control/v1/works/{work_id}", headers=owner, json={"status": "published"}
            ).status_code
            == 422
        )
