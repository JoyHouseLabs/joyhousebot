from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.application.app_callbacks import AppCallbackDispatcher
from joyhousebot.application.app_packs import AppPackService
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.contracts.events import AgentEvent, EventType
from joyhousebot.domain.agents import AgentRevision
from joyhousebot.domain.app_callbacks import callback_signature
from joyhousebot.domain.app_packs import app_manifest_sha256, normalize_app_manifest
from tests.support.postgres_store import PostgresTestStore


def _manifest(version: str = "1.0.0") -> dict:
    return {
        "schema_version": 1,
        "app_id": "app.market-radar",
        "version": version,
        "name": "Market Radar",
        "description": "A continuously running opportunity research application.",
        "publisher": "Joyhouse",
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
    monkeypatch.setenv("JOYHOUSEBOT_CONTROL_TOKEN", "app-control-token")
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
                "endpoint": "https://callbacks.example.com/joyhousebot",
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
    timestamp = request.headers["X-Joyhouse-Timestamp"]
    assert request.headers["Idempotency-Key"] == payload["event_id"]
    assert request.headers["X-Joyhouse-Signature"] == callback_signature(
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
