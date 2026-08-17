from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from porthouse.api.app import create_app
from porthouse.application.agent_teams import AgentTeamService
from porthouse.application.app_callbacks import AppCallbackDispatcher
from porthouse.application.app_packs import AppPackService
from porthouse.application.schedules import MAX_APP_SCHEDULES_PER_INSTALLATION
from porthouse.bootstrap.container import build_api_container
from porthouse.config.schema import Config
from porthouse.contracts.events import AgentEvent, EventType
from porthouse.domain.agent_teams import AgentTeamMember, AgentTeamRevision
from porthouse.domain.agents import AgentRevision
from porthouse.domain.app_callbacks import callback_signature
from porthouse.domain.app_packs import app_manifest_sha256, normalize_app_manifest
from tests.support.postgres_store import PostgresTestStore


def _manifest(version: str = "1.0.0") -> dict:
    return {
        "schema_version": 1,
        "app_id": "app.market-radar",
        "version": version,
        "name": "Market Radar",
        "description": "A continuously running opportunity research application.",
        "publisher": "Porthouse",
        "core": {"min_version": "0.1.2"},
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
                "entrypoint_id": "research",
                "name": "Research an opportunity",
                "default": True,
                "execution": {
                    "mode": "agent",
                    "agent_id": "default",
                    "revision_id": "default:v1",
                },
                "interaction_mode": "background",
            }
        ],
        "integrations": [],
        "permissions": ["runs.submit"],
        "secrets": [],
    }


def test_app_manifest_is_canonical_and_digest_is_stable() -> None:
    first = normalize_app_manifest(_manifest())
    second = normalize_app_manifest({**_manifest(), "permissions": ["runs.submit", "runs.submit"]})
    assert first == second
    assert app_manifest_sha256(first) == app_manifest_sha256(second)


def test_owner_market_routes_are_user_scoped_not_platform_admin_only(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "owner-market-routes.db")
    store.create_api_access_token(
        user_id="product-owner", actor_id="test", token="product-owner-market-token"
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    headers = {"Authorization": "Bearer product-owner-market-token"}
    with client:
        listed = client.get("/v1/apps/market/registries", headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json() == {"items": []}
        admin_listed = client.get("/v1/admin/apps/market/registries", headers=headers)
        assert admin_listed.status_code == 403
        missing_key = client.post(
            "/v1/apps/market/acquisitions",
            headers=headers,
            json={
                "registry_id": "marketreg_example",
                "publisher_id": "pub_example",
                "app_id": "app.example",
                "channel": "stable",
            },
        )
        assert missing_key.status_code == 400
        assert "Idempotency-Key" in missing_key.text
    store.close()


@pytest.mark.asyncio
async def test_app_release_install_upgrade_and_rollback(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "app-packs.db")
    service = AppPackService(store)

    await service.save_draft(_manifest(), actor_id="admin")
    report = await service.validate(
        "app.market-radar", "1.0.0", user_id="opc-user"
    )
    assert report["valid"] is True
    release = await service.publish(
        "app.market-radar",
        "1.0.0",
        actor_id="admin",
        user_id="opc-user",
    )
    assert release["status"] == "published"

    installation = await service.install(
        "app.market-radar",
        "1.0.0",
        user_id="opc-user",
        actor_id="admin",
        configuration={},
        granted_permissions=["runs.submit"],
    )
    assert installation["status"] == "installed"
    installation = await service.transition(
        installation["installation_id"],
        user_id="opc-user",
        actor_id="admin",
        action="activate",
    )
    assert installation["status"] == "active"

    await service.save_draft(_manifest("1.1.0"), actor_id="admin")
    await service.publish(
        "app.market-radar",
        "1.1.0",
        actor_id="admin",
        user_id="opc-user",
    )
    upgraded = await service.install(
        "app.market-radar",
        "1.1.0",
        user_id="opc-user",
        actor_id="admin",
        configuration={},
        granted_permissions=["runs.submit"],
    )
    assert upgraded["version"] == "1.1.0"
    assert upgraded["previous_version"] == "1.0.0"

    rolled_back = await service.transition(
        upgraded["installation_id"],
        user_id="opc-user",
        actor_id="admin",
        action="rollback",
    )
    assert rolled_back["version"] == "1.0.0"
    assert rolled_back["status"] == "disabled"
    events = store.list_app_installation_events(
        upgraded["installation_id"], user_id="opc-user"
    )
    assert {item["event_type"] for item in events} >= {
        "installed",
        "activate",
        "upgraded",
        "rollback",
    }


@pytest.mark.asyncio
async def test_public_app_data_plane_launches_only_active_owner_installation(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "app-data-plane.db")
    service = AppPackService(store)
    await service.save_draft(_manifest(), actor_id="admin")
    await service.publish(
        "app.market-radar",
        "1.0.0",
        actor_id="admin",
        user_id="opc-user",
    )
    installed = await service.install(
        "app.market-radar",
        "1.0.0",
        user_id="opc-user",
        actor_id="admin",
        configuration={},
        granted_permissions=["runs.submit"],
    )
    installation = await service.transition(
        installed["installation_id"],
        user_id="opc-user",
        actor_id="admin",
        action="activate",
    )
    definition = store.get_agent_definition("default")
    assert definition is not None
    store.save_agent_revision(
        definition,
        AgentRevision(
            revision_id="default:v2",
            agent_id="default",
            version=2,
            instructions="A newer default policy must not change the installed App.",
            model_policy={"primary": "test/new-default"},
            status="published",
        ),
    )
    profile = store.get_agent_profile("default")
    assert profile is not None and profile.revision.revision_id == "default:v2"
    store.create_api_access_token(
        user_id="opc-user", actor_id="test", token="opc-app-token"
    )
    store.create_api_access_token(
        user_id="other-user", actor_id="test", token="other-app-token"
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    headers = {"Authorization": "Bearer opc-app-token"}
    with client:
        listed = client.get("/v1/apps", headers=headers)
        assert listed.status_code == 200
        assert [item["installation_id"] for item in listed.json()["items"]] == [
            installation["installation_id"]
        ]
        missing_key = client.post(
            f"/v1/apps/{installation['installation_id']}/runs",
            headers=headers,
            json={"input": {"content": "Find a durable market opportunity"}},
        )
        assert missing_key.status_code == 400
        launched = client.post(
            f"/v1/apps/{installation['installation_id']}/runs",
            headers={**headers, "Idempotency-Key": "market-radar-request-1"},
            json={"input": {"content": "Find a durable market opportunity"}},
        )
        assert launched.status_code == 202, launched.text
        assert launched.headers["location"] == f"/v1/runs/{launched.json()['run_id']}"
        invisible = client.get(
            f"/v1/apps/{installation['installation_id']}",
            headers={"Authorization": "Bearer other-app-token"},
        )
        assert invisible.status_code == 404

    run = store.get_runtime_run(launched.json()["run_id"])
    assert run is not None
    assert run.options["metadata"]["app"] == {
        "installation_id": installation["installation_id"],
        "app_id": "app.market-radar",
        "version": "1.0.0",
        "manifest_sha256": installation["manifest_sha256"],
        "entrypoint_id": "research",
    }
    snapshot = store.get_run_execution_snapshot(run.run_id)
    assert snapshot is not None
    assert snapshot.agent_revision_id == "default:v1"
    span = store.start_execution_span(
        span_id="span-app-usage",
        trace_id="trace-app-usage",
        run_id=run.run_id,
        span_kind="model",
        name="model.generate",
    )
    invocation = store.create_model_invocation(
        invocation_id="model-app-usage",
        run_id=run.run_id,
        span_id=span.span_id,
        provider="test",
        model="test/model-v1",
        operation="generate",
    )
    store.finish_model_invocation(
        invocation.invocation_id,
        usage={"input_tokens": 120, "output_tokens": 30},
        cost_usd=0.0125,
    )
    usage_client = TestClient(
        create_app(build_api_container(config=Config(), store=store))
    )
    with usage_client:
        usage = usage_client.get(
            f"/v1/apps/{installation['installation_id']}/usage", headers=headers
        )
        assert usage.status_code == 200, usage.text
        assert usage.json()["totals"] == {
            "runs": 1,
            "model_invocations": 1,
            "input_tokens": 120,
            "output_tokens": 30,
            "billed_input_tokens": 120,
            "billed_output_tokens": 30,
            "missing_usage_invocations": 0,
            "partial_usage_invocations": 0,
            "missing_billing_invocations": 0,
            "usage_status": "exact",
            "billing_status": "exact",
            "model_cost_usd": 0.0125,
        }
        assert usage.json()["entrypoints"][0]["entrypoint_id"] == "research"


@pytest.mark.asyncio
async def test_app_delegation_is_short_lived_scope_attenuated_and_installation_bound(
    tmp_path, monkeypatch
) -> None:
    store = PostgresTestStore(tmp_path / "app-delegation.db")
    service = AppPackService(store)
    await service.save_draft(_manifest(), actor_id="admin")
    await service.publish(
        "app.market-radar", "1.0.0", actor_id="admin", user_id="opc-user"
    )
    installed = await service.install(
        "app.market-radar",
        "1.0.0",
        user_id="opc-user",
        actor_id="admin",
        configuration={},
        granted_permissions=["runs.submit"],
    )
    installation = await service.transition(
        installed["installation_id"],
        user_id="opc-user",
        actor_id="admin",
        action="activate",
    )
    app_client, app_secret = store.create_app_client(
        app_id="app.market-radar",
        name="Market Radar SaaS",
        allowed_scopes=["apps.read", "apps.launch", "runs.read", "runs.write"],
        actor_id="admin",
    )
    grant = store.create_app_delegation_grant(
        client_id=app_client["client_id"],
        installation_id=installation["installation_id"],
        user_id="opc-user",
        scopes=["apps.read", "apps.launch", "runs.read", "runs.write"],
        expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        actor_id="token:owner",
    )
    store.create_api_access_token(
        user_id="opc-user", actor_id="test", token="opc-owner-token"
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner_headers = {"Authorization": "Bearer opc-owner-token"}
    monkeypatch.setenv("PORTHOUSE_CONTROL_TOKEN", "app-control-token")
    operator_headers = {
        "Authorization": "Bearer app-control-token",
        "X-Impersonate-User-ID": "opc-user",
    }
    with client:
        personal = client.post(
            "/v1/runs",
            headers={**owner_headers, "Idempotency-Key": "personal-run"},
            json={
                "execution": {"mode": "agent", "agent_id": "default"},
                "input": {"content": "private personal work"},
            },
        )
        assert personal.status_code == 202
        exchanged = client.post(
            "/v1/app-auth/token",
            json={
                "client_id": app_client["client_id"],
                "client_secret": app_secret,
                "grant_id": grant["grant_id"],
                "scopes": ["apps.read", "apps.launch", "runs.read", "runs.write"],
                "ttl_seconds": 900,
            },
        )
        assert exchanged.status_code == 200, exchanged.text
        delegated_headers = {
            "Authorization": f"Bearer {exchanged.json()['access_token']}"
        }
        apps = client.get("/v1/apps", headers=delegated_headers)
        assert [item["installation_id"] for item in apps.json()["items"]] == [
            installation["installation_id"]
        ]
        arbitrary = client.post(
            "/v1/runs",
            headers={**delegated_headers, "Idempotency-Key": "forbidden-direct-run"},
            json={
                "execution": {"mode": "agent", "agent_id": "default"},
                "input": {"content": "must be rejected"},
            },
        )
        assert arbitrary.status_code == 403
        app_run = client.post(
            f"/v1/apps/{installation['installation_id']}/runs",
            headers={**delegated_headers, "Idempotency-Key": "delegated-app-run"},
            json={"input": {"content": "research through the installed App"}},
        )
        assert app_run.status_code == 202, app_run.text
        visible = client.get(
            f"/v1/runs/{app_run.json()['run_id']}", headers=delegated_headers
        )
        assert visible.status_code == 200
        private = client.get(
            f"/v1/runs/{personal.json()['run_id']}", headers=delegated_headers
        )
        assert private.status_code == 404
        delegated_usage = client.get(
            f"/v1/apps/{installation['installation_id']}/usage",
            headers=delegated_headers,
        )
        assert delegated_usage.status_code == 403
        rotated = client.post(
            f"/v1/admin/apps/clients/{app_client['client_id']}/rotate-secret",
            headers=operator_headers,
        )
        assert rotated.status_code == 200, rotated.text
        rotated_secret = rotated.json()["client_secret"]
        assert rotated_secret != app_secret
        assert client.get("/v1/apps", headers=delegated_headers).status_code == 401
        rejected_old_secret = client.post(
            "/v1/app-auth/token",
            json={
                "client_id": app_client["client_id"],
                "client_secret": app_secret,
                "grant_id": grant["grant_id"],
                "scopes": ["apps.read"],
            },
        )
        assert rejected_old_secret.status_code == 401
        exchanged_again = client.post(
            "/v1/app-auth/token",
            json={
                "client_id": app_client["client_id"],
                "client_secret": rotated_secret,
                "grant_id": grant["grant_id"],
                "scopes": ["apps.read"],
            },
        )
        assert exchanged_again.status_code == 200, exchanged_again.text
        delegated_headers = {
            "Authorization": f"Bearer {exchanged_again.json()['access_token']}"
        }
        reauthorized = client.post(
            f"/v1/apps/{installation['installation_id']}/delegations",
            headers=owner_headers,
            json={
                "client_id": app_client["client_id"],
                "scopes": ["apps.read"],
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(days=7)
                ).isoformat(),
            },
        )
        assert reauthorized.status_code == 201, reauthorized.text
        assert reauthorized.json()["grant_id"] == grant["grant_id"]
        assert client.get("/v1/apps", headers=delegated_headers).status_code == 401
        revoked = client.delete(
            f"/v1/apps/delegations/{grant['grant_id']}", headers=owner_headers
        )
        assert revoked.status_code == 200


@pytest.mark.asyncio
async def test_app_completion_callback_is_transactional_signed_and_auditable(
    tmp_path, monkeypatch
) -> None:
    store = PostgresTestStore(tmp_path / "app-callbacks.db")
    service = AppPackService(store)
    await service.save_draft(_manifest(), actor_id="admin")
    await service.publish(
        "app.market-radar", "1.0.0", actor_id="admin", user_id="opc-user"
    )
    installed = await service.install(
        "app.market-radar",
        "1.0.0",
        user_id="opc-user",
        actor_id="admin",
        configuration={},
        granted_permissions=["runs.submit"],
    )
    installation = await service.transition(
        installed["installation_id"],
        user_id="opc-user",
        actor_id="admin",
        action="activate",
    )
    store.create_api_access_token(
        user_id="opc-user", actor_id="test", token="opc-callback-token"
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    headers = {"Authorization": "Bearer opc-callback-token"}
    with client:
        registered = client.post(
            f"/v1/apps/{installation['installation_id']}/callbacks",
            headers=headers,
            json={
                "endpoint": "https://callbacks.example.com/porthouse",
                "secret_ref": "env://TEST_APP_CALLBACK_SECRET",
                "events": ["run.completed"],
                "max_attempts": 3,
            },
        )
        assert registered.status_code == 201, registered.text
        launched = client.post(
            f"/v1/apps/{installation['installation_id']}/runs",
            headers={**headers, "Idempotency-Key": "callback-app-run"},
            json={"input": {"content": "produce a market report"}},
        )
        assert launched.status_code == 202
        run_id = launched.json()["run_id"]

    terminal = store.finish_runtime_run(
        run_id,
        status="completed",
        event=AgentEvent(
            run_id=run_id,
            type=EventType.RUN_COMPLETED.value,
            status="completed",
        ),
        result={"content": "private result remains on the Run API"},
    )
    assert terminal is not None
    assert (
        store.finish_runtime_run(
            run_id,
            status="completed",
            event=AgentEvent(
                run_id=run_id,
                type=EventType.RUN_COMPLETED.value,
                status="completed",
            ),
        )
        is None
    )
    pending = store.list_run_app_callback_deliveries(run_id, user_id="opc-user")
    assert len(pending) == 1
    assert "result" not in pending[0]["payload"]["run"]

    secret = "a-callback-secret-that-is-longer-than-thirty-two-bytes"
    monkeypatch.setenv("TEST_APP_CALLBACK_SECRET", secret)
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(503 if len(captured) == 1 else 204, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        dispatcher = AppCallbackDispatcher(store, client=http_client)
        assert await dispatcher.process_next(worker_id="scheduler-test") is True
        retrying = store.list_run_app_callback_deliveries(run_id, user_id="opc-user")
        assert retrying[0]["status"] == "pending"
        assert retrying[0]["attempt"] == 1
        with store._pool.connection() as connection, connection.transaction():
            connection.execute(
                "UPDATE app_callback_outbox SET available_at=clock_timestamp() WHERE run_id=%s",
                (run_id,),
            )
        assert await dispatcher.process_next(worker_id="scheduler-test") is True

    delivered = store.list_run_app_callback_deliveries(run_id, user_id="opc-user")
    assert delivered[0]["status"] == "sent"
    assert delivered[0]["attempt"] == 2
    request = captured[1]
    payload = json.loads(request.content)
    timestamp = request.headers["X-Porthouse-Timestamp"]
    assert request.headers["Idempotency-Key"] == payload["event_id"]
    assert request.headers["X-Porthouse-Signature"] == callback_signature(
        secret.encode(), timestamp=timestamp, body=request.content
    )
    query_client = TestClient(
        create_app(build_api_container(config=Config(), store=store))
    )
    with query_client:
        listed = query_client.get(
            f"/v1/runs/{run_id}/app-callbacks", headers=headers
        )
        assert listed.status_code == 200
        assert listed.json()["items"][0]["status"] == "sent"
        assert "secret_ref" not in listed.json()["items"][0]
        original_event_id = listed.json()["items"][0]["event_id"]
        missing_key = query_client.post(
            f"/v1/runs/{run_id}/app-callbacks/{original_event_id}/replay",
            headers=headers,
        )
        assert missing_key.status_code == 400, missing_key.text
        replayed = query_client.post(
            f"/v1/runs/{run_id}/app-callbacks/{original_event_id}/replay",
            headers={**headers, "Idempotency-Key": "operator-replay-1"},
        )
        assert replayed.status_code == 202, replayed.text
        replay = replayed.json()
        assert replay["event_id"] != original_event_id
        assert replay["replay_of_event_id"] == original_event_id
        assert replay["replay_sequence"] == 1
        same_replay = query_client.post(
            f"/v1/runs/{run_id}/app-callbacks/{original_event_id}/replay",
            headers={**headers, "Idempotency-Key": "operator-replay-1"},
        )
        assert same_replay.status_code == 202
        assert same_replay.json()["event_id"] == replay["event_id"]

    replay_requests: list[httpx.Request] = []

    def replay_handler(request: httpx.Request) -> httpx.Response:
        replay_requests.append(request)
        return httpx.Response(204, request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(replay_handler)
    ) as replay_client:
        dispatcher = AppCallbackDispatcher(store, client=replay_client)
        assert await dispatcher.process_next(worker_id="scheduler-replay") is True
    replay_payload = json.loads(replay_requests[0].content)
    assert replay_requests[0].headers["Idempotency-Key"] == replay_payload["event_id"]
    assert replay_payload["delivery"] == {
        "replay_of_event_id": original_event_id,
        "replay_sequence": 1,
    }


async def _active_installation_with_client(store, *, scopes: list[str]):
    """Publish + activate the Market Radar app and create a delegated client."""

    service = AppPackService(store)
    manifest = {
        **_manifest(),
        "permissions": ["runs.submit", "schedules.submit"],
    }
    await service.save_draft(manifest, actor_id="admin")
    await service.publish(
        "app.market-radar", "1.0.0", actor_id="admin", user_id="opc-user"
    )
    installed = await service.install(
        "app.market-radar",
        "1.0.0",
        user_id="opc-user",
        actor_id="admin",
        configuration={},
        granted_permissions=["runs.submit", "schedules.submit"],
    )
    installation = await service.transition(
        installed["installation_id"],
        user_id="opc-user",
        actor_id="admin",
        action="activate",
    )
    app_client, app_secret = store.create_app_client(
        app_id="app.market-radar",
        name="Market Radar SaaS",
        allowed_scopes=scopes,
        actor_id="admin",
    )
    grant = store.create_app_delegation_grant(
        client_id=app_client["client_id"],
        installation_id=installation["installation_id"],
        user_id="opc-user",
        scopes=scopes,
        expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        actor_id="token:owner",
    )
    store.create_api_access_token(
        user_id="opc-user", actor_id="test", token="opc-owner-token"
    )
    return service, installation, (app_client, app_secret, grant)


def _app_schedule_body(name: str = "radar refresh") -> dict:
    return {
        "name": name,
        "schedule": {"kind": "every", "every_ms": 300_000},
        "payload": {"kind": "app_entrypoint", "entrypoint_id": "research"},
        "enabled": True,
    }


@pytest.mark.asyncio
async def test_app_schedule_endpoint_creates_lists_and_deduplicates(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "app-schedules.db")
    _service, installation, _client = await _active_installation_with_client(
        store, scopes=["apps.read", "apps.schedules"]
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner = {"Authorization": "Bearer opc-owner-token"}
    with client:
        missing_key = client.post(
            f"/v1/apps/{installation['installation_id']}/schedules",
            headers=owner,
            json=_app_schedule_body(),
        )
        assert missing_key.status_code == 400
        assert "Idempotency-Key" in missing_key.text
        created = client.post(
            f"/v1/apps/{installation['installation_id']}/schedules",
            headers={**owner, "Idempotency-Key": "schedule-1"},
            json=_app_schedule_body(),
        )
        assert created.status_code == 201, created.text
        row = created.json()
        assert row["payload"]["kind"] == "app_entrypoint"
        assert row["installation_id"] == installation["installation_id"]
        assert row["policy"]["misfire_policy"] == "skip"
        assert row["policy"]["overlap_policy"] == "skip"
        replayed = client.post(
            f"/v1/apps/{installation['installation_id']}/schedules",
            headers={**owner, "Idempotency-Key": "schedule-1"},
            json=_app_schedule_body("other name"),
        )
        assert replayed.status_code == 201
        assert replayed.json()["id"] == row["id"]
        listed = client.get(
            f"/v1/apps/{installation['installation_id']}/schedules",
            headers=owner,
        )
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [row["id"]]
        wrong_payload = client.post(
            f"/v1/apps/{installation['installation_id']}/schedules",
            headers={**owner, "Idempotency-Key": "schedule-2"},
            json={
                "name": "not an app schedule",
                "schedule": {"kind": "every", "every_ms": 300_000},
                "payload": {"kind": "agent_turn", "message": "hi"},
            },
        )
        assert wrong_payload.status_code == 422
    store.close()


@pytest.mark.asyncio
async def test_app_schedule_requires_active_installation_and_enforces_quota(
    tmp_path,
) -> None:
    store = PostgresTestStore(tmp_path / "app-schedule-quota.db")
    service, installation, _client = await _active_installation_with_client(
        store, scopes=["apps.read"]
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner = {"Authorization": "Bearer opc-owner-token"}
    installation_id = installation["installation_id"]
    with client:
        for index in range(MAX_APP_SCHEDULES_PER_INSTALLATION):
            created = client.post(
                f"/v1/apps/{installation_id}/schedules",
                headers={**owner, "Idempotency-Key": f"quota-{index}"},
                json=_app_schedule_body(f"refresh {index}"),
            )
            assert created.status_code == 201, created.text
        exceeded = client.post(
            f"/v1/apps/{installation_id}/schedules",
            headers={**owner, "Idempotency-Key": "quota-over"},
            json=_app_schedule_body(),
        )
        assert exceeded.status_code == 409
        assert "schedule limit" in exceeded.text
        await service.transition(
            installation_id,
            user_id="opc-user",
            actor_id="admin",
            action="disable",
        )
        drifted = client.post(
            f"/v1/apps/{installation_id}/schedules",
            headers={**owner, "Idempotency-Key": "after-disable"},
            json=_app_schedule_body(),
        )
        assert drifted.status_code == 409
    store.close()


@pytest.mark.asyncio
async def test_delegated_app_schedule_scope_and_installation_isolation(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "app-schedule-delegation.db")
    _service, installation, (app_client, app_secret, grant) = (
        await _active_installation_with_client(
            store, scopes=["apps.read", "apps.schedules"]
        )
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    with client:
        exchanged = client.post(
            "/v1/app-auth/token",
            json={
                "client_id": app_client["client_id"],
                "client_secret": app_secret,
                "grant_id": grant["grant_id"],
                "scopes": ["apps.read", "apps.schedules"],
            },
        )
        assert exchanged.status_code == 200, exchanged.text
        token = exchanged.json()["access_token"]
        delegated = {"Authorization": f"Bearer {token}"}
        created = client.post(
            f"/v1/apps/{installation['installation_id']}/schedules",
            headers={**delegated, "Idempotency-Key": "delegated-1"},
            json=_app_schedule_body(),
        )
        assert created.status_code == 201, created.text
        isolated = client.post(
            "/v1/apps/appinst-someone-else/schedules",
            headers={**delegated, "Idempotency-Key": "delegated-2"},
            json=_app_schedule_body(),
        )
        assert isolated.status_code == 404
        listed = client.get(
            f"/v1/apps/{installation['installation_id']}/schedules",
            headers=delegated,
        )
        assert listed.status_code == 200
    store.close()


@pytest.mark.asyncio
async def test_delegated_token_without_schedule_scope_is_forbidden(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "app-schedule-scope-denied.db")
    _service, installation, (app_client, app_secret, grant) = (
        await _active_installation_with_client(
            store, scopes=["apps.read", "apps.launch"]
        )
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    with client:
        exchanged = client.post(
            "/v1/app-auth/token",
            json={
                "client_id": app_client["client_id"],
                "client_secret": app_secret,
                "grant_id": grant["grant_id"],
                "scopes": ["apps.read"],
            },
        )
        assert exchanged.status_code == 200
        token = exchanged.json()["access_token"]
        denied = client.post(
            f"/v1/apps/{installation['installation_id']}/schedules",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": "denied-1",
            },
            json=_app_schedule_body(),
        )
        assert denied.status_code == 403
        assert "apps.schedules" in denied.text
    store.close()


@pytest.mark.asyncio
async def test_installation_transitions_toggle_app_schedules(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "app-schedule-toggle.db")
    service, installation, _client = await _active_installation_with_client(
        store, scopes=["apps.read"]
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))
    owner = {"Authorization": "Bearer opc-owner-token"}
    installation_id = installation["installation_id"]
    with client:
        created = client.post(
            f"/v1/apps/{installation_id}/schedules",
            headers={**owner, "Idempotency-Key": "toggle-1"},
            json=_app_schedule_body(),
        )
        assert created.status_code == 201
        schedule_id = created.json()["id"]
        await service.transition(
            installation_id, user_id="opc-user", actor_id="admin", action="disable"
        )
        after_disable = client.get(
            f"/v1/apps/{installation_id}/schedules", headers=owner
        ).json()["items"]
        target = next(item for item in after_disable if item["id"] == schedule_id)
        assert target["enabled"] is False
        await service.transition(
            installation_id, user_id="opc-user", actor_id="admin", action="activate"
        )
        after_enable = client.get(
            f"/v1/apps/{installation_id}/schedules", headers=owner
        ).json()["items"]
        target = next(item for item in after_enable if item["id"] == schedule_id)
        assert target["enabled"] is True
    store.close()


def _teaching_team() -> AgentTeamRevision:
    def member(member_id: str, *, delegate: bool = False) -> AgentTeamMember:
        return AgentTeamMember(
            member_id=member_id,
            agent_id="default",
            agent_revision_id="default:v1",
            role=member_id,
            responsibility="教学方案专家组成员职责。",
            can_delegate=delegate,
            allowed_handoffs=("psychologist", "curriculum_designer", "game_designer", "reviewer") if delegate else (),
        )

    return AgentTeamRevision(
        team_id="team.teaching-plan",
        revision_id="team.teaching-plan:v1",
        version=1,
        name="教学方案专家组",
        description="四位专家协作产出可确认的教学方案。",
        coordinator_member_id="coordinator",
        members=(
            member("coordinator", delegate=True),
            member("psychologist"),
            member("curriculum_designer"),
            member("game_designer"),
            member("reviewer"),
        ),
        budget_policy={"max_tasks": 16, "max_parallel_tasks": 4, "max_handoffs": 16},
        collaboration_blueprint={
            "preset": "parallel_review_revise_synthesize",
            "role_bindings": {
                "producers": ["psychologist", "curriculum_designer", "game_designer"],
                "reviewers": ["reviewer"],
            },
            "guardrails": {"require_plan_confirmation": True},
        },
        status="draft",
        created_by="admin",
    )


def _teaching_manifest() -> dict:
    return json.loads(
        (Path(__file__).parent / "fixtures" / "app_pack_teaching_plan.json").read_text()
    )


@pytest.mark.asyncio
async def test_team_pack_install_validates_collaboration_blueprint(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "teaching-pack.db")
    service = AppPackService(store)
    await AgentTeamService(store).save_draft(_teaching_team())
    await AgentTeamService(store).publish(
        "team.teaching-plan", "team.teaching-plan:v1", actor_id="admin"
    )
    await service.save_draft(_teaching_manifest(), actor_id="admin")

    report = await service.validate("app.teaching-plan", "1.0.0", user_id="app-user")
    assert report["valid"], report["errors"]
    team_lock = report["dependency_lock"]["teams"]
    assert [item["team_id"] for item in team_lock] == ["team.teaching-plan"]
    assert any(item["kind"] == "team_blueprint" for item in report["checks"])

    # A tampered published blueprint (bypassing the domain validator) must
    # fail closed at install time instead of shipping a broken Team Pack.
    with store._pool.connection() as conn:
        conn.execute(
            """UPDATE agent_team_revisions SET definition=jsonb_set(
                   definition, '{collaboration_blueprint,preset}', '"unknown_preset"')
               WHERE revision_id='team.teaching-plan:v1'""",
        )
    tampered = await service.validate("app.teaching-plan", "1.0.0", user_id="app-user")
    assert not tampered["valid"]
    # The corrupt definition surfaces as a failed team/blueprint check, never
    # as an unhandled server error.
    assert any(
        "team" in item or "blueprint" in item for item in tampered["errors"]
    ), tampered["errors"]
