from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.application.app_releases import AppReleaseService
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.runtime.action_identity import payload_hash
from joyhousebot.runtime.models import AgentEvent, EventVisibility
from tests.support.postgres_store import PostgresTestStore


def _manifest() -> dict:
    return {
        "schema_version": 1,
        "app_id": "app.talent-flow",
        "version": "2.0.0",
        "name": "Talent Flow",
        "description": "Recruiting workflow application.",
        "publisher": "JoyHouse",
        "core": {"min_version": "2.0.0"},
        "extensions": [],
        "capabilities": [],
        "assets": {
            "agents": [{"agent_id": "default", "revision_id": "default:v1"}],
            "teams": [],
            "skills": [],
            "workflows": [],
            "scenarios": [],
        },
        "entrypoints": [
            {
                "entrypoint_id": "candidate-review",
                "name": "Candidate review",
                "default": True,
                "execution": {
                    "mode": "agent",
                    "agent_id": "default",
                    "revision_id": "default:v1",
                },
                "interaction_mode": "background",
                "input_schema": {
                    "type": "object",
                    "properties": {"candidate_id": {"type": "string"}},
                    "required": ["candidate_id"],
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object"},
            }
        ],
        "connections": [],
        "permissions": ["runs.submit"],
        "secrets": [],
    }


async def _install_app(store: PostgresTestStore) -> dict:
    service = AppReleaseService(store)
    await service.save_draft(_manifest(), actor_id="builder")
    await service.publish(
        "app.talent-flow",
        "2.0.0",
        actor_id="builder",
        user_id="owner-a",
    )
    installed = await service.install(
        "app.talent-flow",
        "2.0.0",
        user_id="owner-a",
        actor_id="owner-a",
        configuration={},
        granted_permissions=["runs.submit"],
    )
    return await service.transition(
        installed["installation_id"],
        user_id="owner-a",
        actor_id="owner-a",
        action="activate",
    )


async def _publish_app(store: PostgresTestStore) -> None:
    service = AppReleaseService(store)
    await service.save_draft(_manifest(), actor_id="builder")
    await service.publish(
        "app.talent-flow",
        "2.0.0",
        actor_id="builder",
        user_id="owner-a",
    )


@pytest.mark.asyncio
async def test_owner_installs_app_without_operator_or_installation_credentials(
    tmp_path,
) -> None:
    store = PostgresTestStore(tmp_path / "owner-app-install.db")
    await _publish_app(store)
    store.create_api_access_token(
        user_id="owner-a",
        actor_id="test",
        token="owner-app-token",
        scopes=["apps.read", "apps.install"],
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    headers = {"Authorization": "Bearer owner-app-token"}

    with client:
        installed = client.post(
            "/v2/apps/app.talent-flow/install",
            headers=headers,
            json={"version": "2.0.0", "configuration": {}},
        )
        assert installed.status_code == 201, installed.text
        assert installed.json()["status"] == "active"
        listed = client.get("/v2/apps", headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["app_id"] == "app.talent-flow"

    store.close()


def _installation_credentials(store: PostgresTestStore, installation_id: str) -> tuple[dict, str]:
    app_client, secret = store.create_app_client(
        app_id="app.talent-flow",
        name="Talent Flow backend",
        allowed_scopes=["apps.read", "apps.launch", "runs.read", "runs.write"],
        actor_id="builder",
    )
    store.create_app_delegation_grant(
        client_id=app_client["client_id"],
        installation_id=installation_id,
        user_id="owner-a",
        scopes=["apps.read", "apps.launch", "runs.read", "runs.write"],
        expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        actor_id="owner-a",
    )
    return app_client, secret


def _create_owner_run(
    store: PostgresTestStore,
    run_id: str,
    *,
    user_id: str = "owner-a",
    status: str = "queued",
) -> None:
    store.create_runtime_run(
        run_id=run_id,
        user_id=user_id,
        session_id=f"session-{run_id}",
        agent_id="default",
        kind="agent",
        prompt="public execution test",
        options={},
        initial_status=status,
    )


def _create_pending_approval(store: PostgresTestStore, run_id: str) -> str:
    _create_owner_run(store, run_id)
    run = store.claim_runtime_run(run_id, worker_id="test-worker")
    assert run is not None
    action_id = f"action-{run_id}"
    turn_id = f"turn-{run_id}"
    capability_ref = {
        "capability_id": "publish_result",
        "version": "1.0.0",
        "kind": "capability",
        "extension_id": "test.publisher",
        "extension_version": "1.0.0",
        "build_digest": "sha256:test",
    }
    inputs = {"target": "https://example.test/post"}
    store.create_runtime_turn(
        turn_id=turn_id,
        run_id=run_id,
        task_id=None,
        turn_index=0,
        model="test/default",
        request_hash="test-request",
        worker_id="test-worker",
    )
    store.create_action_intent(
        action_id=action_id,
        turn_id=turn_id,
        run_id=run_id,
        task_id=None,
        turn_index=0,
        action_index=0,
        capability_ref=capability_ref,
        input=inputs,
        input_hash=payload_hash(inputs),
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
        user_id="owner-a",
        capability_ref=capability_ref,
        input_hash=payload_hash(inputs),
        input_preview=inputs,
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
    return approval.approval_id


@pytest.mark.asyncio
async def test_v2_entrypoint_launch_uses_structured_input_and_hides_target(
    tmp_path, monkeypatch
) -> None:
    store = PostgresTestStore(tmp_path / "public-v2.db")
    installation = await _install_app(store)
    app_client, secret = _installation_credentials(store, installation["installation_id"])
    store.create_api_access_token(user_id="owner-a", actor_id="test", token="owner-a-token")
    store.create_runtime_run(
        run_id="run-other-installation",
        user_id="owner-a",
        session_id="other-installation-session",
        agent_id="default",
        kind="agent",
        prompt="private to another installation",
        options={"metadata": {"app": {"installation_id": "installation-other"}}},
        initial_status="queued",
    )
    monkeypatch.setenv("JOYHOUSEBOT_CONTROL_TOKEN", "operator-control-token")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))

    with client:
        exchanged = client.post(
            "/v2/app-auth/token",
            json={
                "client_id": app_client["client_id"],
                "client_secret": secret,
                "installation_id": installation["installation_id"],
                "scopes": ["apps.read", "apps.launch", "runs.read", "runs.write"],
            },
        )
        assert exchanged.status_code == 200, exchanged.text
        assert "grant_id" not in exchanged.json()
        app_headers = {"Authorization": f"Bearer {exchanged.json()['access_token']}"}

        listed = client.get("/v2/entrypoints", headers=app_headers)
        assert listed.status_code == 200, listed.text
        entrypoint = listed.json()["items"][0]
        assert entrypoint["id"].endswith(":candidate-review")
        assert entrypoint["key"] == "candidate-review"
        assert entrypoint["app_id"] == "app.talent-flow"
        assert "execution" not in entrypoint
        assert entrypoint["input_schema"]["required"] == ["candidate_id"]

        rejected = client.post(
            f"/v2/entrypoints/{entrypoint['id']}/runs",
            headers=app_headers,
            json={"input": {}, "idempotency_key": "review-request-invalid"},
        )
        assert rejected.status_code == 422, rejected.text
        assert "candidate_id" in rejected.text

        launched = client.post(
            f"/v2/entrypoints/{entrypoint['id']}/runs",
            headers=app_headers,
            json={
                "input": {"candidate_id": "candidate-42"},
                "idempotency_key": "review-request-42",
                "client_context": {
                    "request_id": "review-42",
                    "app": {"installation_id": "forged-installation"},
                    "user_id": "forged-user",
                },
            },
        )
        assert launched.status_code == 202, launched.text
        assert launched.headers["location"].startswith("/v2/runs/")
        assert launched.json()["status"] == "queued"

        repeated = client.post(
            f"/v2/entrypoints/{entrypoint['id']}/runs",
            headers=app_headers,
            json={
                "input": {"candidate_id": "candidate-42"},
                "idempotency_key": "review-request-42",
                "client_context": {"request_id": "review-42"},
            },
        )
        assert repeated.status_code == 202, repeated.text
        assert repeated.json()["id"] == launched.json()["id"]

        fetched = client.get(f"/v2/runs/{launched.json()['id']}", headers=app_headers)
        assert fetched.status_code == 200
        assert fetched.json()["id"] == launched.json()["id"]

        foreign_run = client.get("/v2/runs/run-other-installation", headers=app_headers)
        assert foreign_run.status_code == 404
        foreign_cancel = client.post("/v2/runs/run-other-installation/cancel", headers=app_headers)
        assert foreign_cancel.status_code == 404
        personal_memory = client.get("/control/v1/memory/documents?agent_id=default", headers=app_headers)
        assert personal_memory.status_code == 403
        forged_scope = client.post(
            f"/v2/entrypoints/{entrypoint['id']}/runs",
            headers=app_headers,
            json={
                "input": {"candidate_id": "candidate-forged"},
                "idempotency_key": "review-request-forged",
                "user_id": "owner-b",
                "installation_id": "installation-other",
            },
        )
        assert forged_scope.status_code == 422
        assert forged_scope.json()["error"]["retryable"] is False

        stored = store.get_runtime_run(launched.json()["id"], expected_user_id="owner-a")
        assert stored is not None
        assert json.loads(stored.prompt) == {"candidate_id": "candidate-42"}
        assert (
            stored.options["metadata"]["app"]["installation_id"] == installation["installation_id"]
        )
        assert stored.options["metadata"]["client_context"]["app"] == {
            "installation_id": "forged-installation"
        }

        operator = client.get(
            "/v2/entrypoints",
            headers={
                "Authorization": "Bearer operator-control-token",
                "X-Impersonate-User-ID": "owner-a",
                "X-Impersonation-Reason": "Verify public surface authority gate",
            },
        )
        assert operator.status_code == 403
        assert "Control credentials are accepted only on the control API" in operator.text

        grant = store.list_app_delegation_grants(
            installation_id=installation["installation_id"], user_id="owner-a"
        )[0]
        assert store.revoke_app_delegation_grant(
            grant["grant_id"], user_id="owner-a", actor_id="owner-a"
        )
        revoked = client.get("/v2/entrypoints", headers=app_headers)
        assert revoked.status_code == 401

    store.close()


@pytest.mark.asyncio
async def test_v2_owner_can_list_entrypoints_but_idempotency_sources_must_match(
    tmp_path,
) -> None:
    store = PostgresTestStore(tmp_path / "owner-public-v2.db")
    await _install_app(store)
    store.create_api_access_token(user_id="owner-a", actor_id="test", token="owner-public-token")
    store.upsert_platform_admin(
        user_id="operator-a",
        role="operator",
        permissions=["*"],
        actor_id="test",
    )
    store.create_api_access_token(
        user_id="operator-a",
        actor_id="test",
        token="limited-admin-owner-token",
        scopes=["apps.read"],
    )
    store.create_api_access_token(
        user_id="operator-a",
        actor_id="test",
        token="operator-api-token",
        principal_kind="operator",
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    headers = {"Authorization": "Bearer owner-public-token"}

    with client:
        listed = client.get("/v2/entrypoints", headers=headers)
        assert listed.status_code == 200
        entrypoint_id = listed.json()["items"][0]["id"]
        conflict = client.post(
            f"/v2/entrypoints/{entrypoint_id}/runs",
            headers={**headers, "Idempotency-Key": "header-key"},
            json={
                "input": {"candidate_id": "candidate-1"},
                "idempotency_key": "body-key",
            },
        )
        assert conflict.status_code == 409

        limited_admin = client.get(
            "/v2/entrypoints",
            headers={"Authorization": "Bearer limited-admin-owner-token"},
        )
        assert limited_admin.status_code == 200
        operator_api = client.get(
            "/v2/entrypoints",
            headers={"Authorization": "Bearer operator-api-token"},
        )
        assert operator_api.status_code == 403

    store.close()


def test_v2_run_inputs_artifacts_and_cancel_are_owner_scoped(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "public-v2-resources.db")
    store.create_api_access_token(user_id="owner-a", actor_id="test", token="owner-resource-token")
    store.create_api_access_token(
        user_id="owner-b", actor_id="test", token="foreign-resource-token"
    )
    _create_owner_run(store, "run-input", status="waiting_input")
    store.create_input_request(
        input_request_id="input-run-input",
        run_id="run-input",
        user_id="owner-a",
        scenario_id="__dynamic__",
        scenario_version=1,
        node_id="agent:input-run-input",
        question="Which deadline should be used?",
        fields=[{"name": "deadline", "value_type": "string", "required": True}],
        presentation={"help_text": "Use ISO date format."},
        source="agent",
    )
    store.add_runtime_artifact(
        artifact_id="artifact-public-output",
        run_id="run-input",
        name="Review result",
        media_type="application/json",
        content={"score": 0.91},
        uri="joyhousebot-blob://private-location",
        artifact_type="talent.review",
        metadata={"internal_note": "must not cross the public boundary"},
        provenance={"worker_id": "private-worker"},
        evidence={"trace_id": "private-trace"},
    )
    _create_owner_run(store, "run-cancel")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    headers = {"Authorization": "Bearer owner-resource-token"}

    with client:
        inputs = client.get("/v2/runs/run-input/inputs", headers=headers)
        assert inputs.status_code == 200, inputs.text
        assert inputs.json()["items"][0]["question"].startswith("Which deadline")
        resolved = client.post(
            "/v2/runs/run-input/inputs",
            headers=headers,
            json={
                "input_request_id": "input-run-input",
                "answers": {"deadline": "2026-09-01"},
            },
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["run"]["status"] == "queued"
        assert resolved.json()["pending_inputs"] == []

        listed = client.get("/v2/runs/run-input/artifacts", headers=headers)
        assert listed.status_code == 200, listed.text
        artifact = listed.json()["items"][0]
        assert artifact["content"] == {"score": 0.91}
        assert not {"uri", "metadata", "provenance", "evidence"} & artifact.keys()
        fetched = client.get("/v2/artifacts/artifact-public-output", headers=headers)
        assert fetched.status_code == 200
        foreign = client.get(
            "/v2/artifacts/artifact-public-output",
            headers={"Authorization": "Bearer foreign-resource-token"},
        )
        assert foreign.status_code == 404

        cancelled = client.post("/v2/runs/run-cancel/cancel", headers=headers)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"

    store.close()


def test_v2_approval_decision_hides_frozen_action_internals(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "public-v2-approval.db")
    store.create_api_access_token(user_id="owner-a", actor_id="test", token="owner-approval-token")
    approval_id = _create_pending_approval(store, "run-approval-public")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    headers = {"Authorization": "Bearer owner-approval-token"}

    with client:
        listed = client.get("/v2/runs/run-approval-public/approvals", headers=headers)
        assert listed.status_code == 200, listed.text
        approval = listed.json()["items"][0]
        assert approval["allowed_decisions"] == [
            "approve",
            "reject",
            "request_changes",
        ]
        assert approval["input_preview"]["target"].startswith("https://")
        assert not {"action_id", "capability_ref", "input_hash", "required_role"} & approval.keys()

        decided = client.post(
            f"/v2/approvals/{approval_id}/decisions",
            headers=headers,
            json={"decision": "approve", "note": "Owner confirmed."},
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["approval"]["status"] == "approved"
        assert decided.json()["run"]["status"] == "queued"

    store.close()


def test_v2_sse_resumes_from_last_event_and_filters_internal_events(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "public-v2-events.db")
    store.create_api_access_token(user_id="owner-a", actor_id="test", token="owner-events-token")
    _create_owner_run(store, "run-events")
    first = store.append_runtime_event(
        AgentEvent(run_id="run-events", type="run.started", status="running")
    )
    store.append_runtime_event(
        AgentEvent(
            run_id="run-events",
            type="message.delta",
            data={"content": "private output"},
            visibility=EventVisibility.PRIVATE.value,
        )
    )
    store.append_runtime_event(
        AgentEvent(run_id="run-events", type="model.request.started", data={})
    )
    delta = store.append_runtime_event(
        AgentEvent(
            run_id="run-events",
            type="message.delta",
            data={"content": "public output"},
        )
    )
    terminal = store.append_runtime_event(
        AgentEvent(run_id="run-events", type="run.completed", status="completed")
    )
    assert store.update_runtime_run("run-events", status="completed")
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))

    with client:
        response = client.get(
            "/v2/runs/run-events/events?after_sequence=0",
            headers={
                "Authorization": "Bearer owner-events-token",
                "Last-Event-ID": str(first.sequence),
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "private output" not in response.text
        assert "model.request.started" not in response.text
        assert "event: run.output.delta" in response.text
        assert "public output" in response.text
        assert "event: run.succeeded" in response.text
        assert f"id: {delta.sequence}" in response.text
        assert f"id: {terminal.sequence}" in response.text
        assert f"id: {first.sequence}" not in response.text

        invalid = client.get(
            "/v2/runs/run-events/events",
            headers={
                "Authorization": "Bearer owner-events-token",
                "Last-Event-ID": "not-an-integer",
            },
        )
        assert invalid.status_code == 400

    store.close()
