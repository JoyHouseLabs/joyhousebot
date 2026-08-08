import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.domain.capabilities import CapabilityDefinition, CapabilityKind, CapabilityRef
from joyhousebot.domain.scenarios import ClarificationNode, ScenarioField, ScenarioVersion
from tests.support.postgres_store import PostgresTestStore


def _client(tmp_path: Path) -> tuple[TestClient, PostgresTestStore]:
    config = Config()
    store = PostgresTestStore(tmp_path / "cloud.db")
    store.create_api_access_token(
        user_id="user-a", actor_id="test", token="token-a"
    )
    store.create_api_access_token(
        user_id="user-b", actor_id="test", token="token-b"
    )
    container = build_api_container(config=config, store=store)
    return TestClient(create_app(container)), store


def test_public_and_control_http_surfaces_are_deployable_separately() -> None:
    public_paths = {route.path for route in create_app(surface="public").routes}
    control_paths = {route.path for route in create_app(surface="control").routes}
    assert "/v1/runs" in public_paths
    assert "/v1/admin/overview" not in public_paths
    assert "/v1/admin/overview" in control_paths
    assert "/v1/runs" not in control_paths


def test_production_rejects_combined_or_insecure_api_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYHOUSEBOT_ENVIRONMENT", "production")
    monkeypatch.delenv("JOYHOUSEBOT_ALLOW_COMBINED_SURFACE", raising=False)
    monkeypatch.delenv("JOYHOUSEBOT_CONTROL_TOKEN", raising=False)
    monkeypatch.delenv("JOYHOUSEBOT_METRICS_TOKEN", raising=False)
    with pytest.raises(ValueError, match="separate public and control"):
        create_app(surface="combined")

    config = Config()
    config.gateway.allow_insecure_auth = True
    store = PostgresTestStore(tmp_path / "production-security.db")
    container = build_api_container(config=config, store=store)
    with pytest.raises(ValueError, match="allow_insecure_auth"):
        create_app(container, surface="public")
    store.close()


def test_scoped_service_token_attenuates_user_api_access(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    record, token = store.create_api_access_token(
        user_id="service-a",
        actor_id="test",
        label="read-only-run-exporter",
        token_type="service",
        scopes=["runs.read"],
        expires_at=expires_at,
    )
    headers = {"Authorization": f"Bearer {token}"}
    with client:
        assert client.get("/v1/runs", headers=headers).status_code == 200
        denied = client.post(
            "/v1/runs",
            headers=headers,
            json={"input": {"content": "must not execute"}},
        )
        assert denied.status_code == 403
        assert denied.json()["detail"] == "API token scope required: runs.write"
        identity = client.get("/v1/me", headers=headers)
        assert identity.status_code == 403

    assert record["token_type"] == "service"
    assert record["scopes"] == ["runs.read"]
    events = store.list_api_access_token_events(limit=10)
    issued = next(item for item in events if item["token_id"] == record["token_id"])
    assert issued["data"]["scopes"] == ["runs.read"]


def test_prometheus_metrics_endpoint_exposes_runtime_families(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYHOUSEBOT_METRICS_TOKEN", "scrape-token")
    client, store = _client(tmp_path)
    with client:
        response = client.get("/metrics", headers={"Authorization": "Bearer scrape-token"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "joyhousebot_up 1" in response.text
    assert "joyhousebot_runs_total" in response.text
    assert "joyhousebot_tasks_total" in response.text
    assert "joyhousebot_task_claim_delay_ms_p95" in response.text
    assert "joyhousebot_approval_oldest_pending_seconds" in response.text
    assert "joyhousebot_reconciliation_oldest_active_seconds" in response.text


def test_prometheus_metrics_fails_closed_without_configured_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JOYHOUSEBOT_METRICS_TOKEN", raising=False)
    client, _ = _client(tmp_path)
    with client:
        response = client.get("/metrics")
    assert response.status_code == 404
    assert "joyhousebot_up" not in response.text


def test_prometheus_metrics_requires_bearer_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOYHOUSEBOT_METRICS_TOKEN", "scrape-token")
    client, _ = _client(tmp_path)
    with client:
        assert client.get("/metrics").status_code == 401
        assert (
            client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        assert (
            client.get("/metrics", headers={"Authorization": "Bearer scrape-token"}).status_code
            == 200
        )


def test_scenario_studio_is_role_scoped_and_versions_are_immutable(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    store.upsert_platform_admin(
        user_id="root-admin", permissions=["*"], actor_id="test-bootstrap"
    )
    store.upsert_platform_admin(
        user_id="business-editor",
        role="operator",
        permissions=["scenarios.read", "scenarios.write", "scenarios.publish"],
        actor_id="test",
    )
    store.create_api_access_token(
        user_id="business-editor", actor_id="test", token="editor-token"
    )
    store.publish_capability(
        CapabilityDefinition(
            ref=CapabilityRef("speech.synthesize", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test"),
            name="Speech synthesis",
            description="Generate audio",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            adapter="builtin.speech",
        )
    )
    payload = {
        "version": 1,
        "name": "语音合成",
        "fields": [
            {
                "name": "voice",
                "value_type": "string",
                "required": True,
                "enum": ["default", "pro"],
            }
        ],
        "nodes": [
            {
                "node_id": "ask_voice",
                "kind": "question",
                "question": "请选择声音",
                "field_names": ["voice"],
            }
        ],
        "allowed_capabilities": [{"capability_id": "speech.synthesize", "version": "1.0.0", "kind": "tool", "plugin_id": "test.plugin", "plugin_version": "1.0.0", "plugin_build_digest": "sha256:test"}],
        "planning_mode": "fixed",
        "routing_rules": [{"contains_any": ["语音", "朗读"]}],
    }
    editor = {"Authorization": "Bearer editor-token"}
    with client:
        assert (
            client.get(
                "/v1/admin/scenarios", headers={"Authorization": "Bearer token-a"}
            ).status_code
            == 403
        )
        saved = client.put("/v1/admin/scenarios/tts/versions/1", headers=editor, json=payload)
        assert saved.status_code == 200
        assert saved.json()["status"] == "draft"

        simulation = client.post(
            "/v1/admin/scenarios/tts/simulate",
            headers=editor,
            json={"prompt": "帮我生成语音", "inputs": {}},
        )
        assert simulation.status_code == 200
        assert simulation.json()["next_question"]["fields"] == ["voice"]

        published = client.post("/v1/admin/scenarios/tts/versions/1/publish", headers=editor)
        assert published.status_code == 200
        assert published.json()["status"] == "published"

        changed = {**payload, "name": "被篡改"}
        immutable = client.put("/v1/admin/scenarios/tts/versions/1", headers=editor, json=changed)
        assert immutable.status_code == 409
        listed = client.get("/v1/admin/scenarios", headers=editor).json()["items"]
        assert listed[0]["status"] == "published"


def test_api_is_submit_only_and_user_scoped(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    with client:
        response = client.post(
            "/v1/runs",
            headers={
                "Authorization": "Bearer token-a",
                "Idempotency-Key": "one",
                "X-Request-Id": "req-api-test",
                "X-Tracker-Id": "trace-api-test",
            },
            json={
                "agent_id": "default",
                "session_id": "main",
                "input": {"type": "message", "content": "hello"},
            },
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        assert response.json()["status"] == "queued"
        stored = store.get_runtime_run(run_id)
        assert stored.lease_owner is None
        assert stored.options["request_id"] == "req-api-test"
        assert stored.options["tracker_id"] == "trace-api-test"
        assert response.headers["x-request-id"] == "req-api-test"
        assert response.headers["x-tracker-id"] == "trace-api-test"

        own = client.get(f"/v1/runs/{run_id}", headers={"Authorization": "Bearer token-a"})
        other = client.get(f"/v1/runs/{run_id}", headers={"Authorization": "Bearer token-b"})
        assert own.status_code == 200
        assert other.status_code == 404


def test_api_replaces_invalid_request_tracking_headers(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        response = client.get(
            "/healthz",
            headers={"X-Request-Id": "invalid request id value", "X-Tracker-Id": "*"},
        )
    assert response.status_code == 200
    assert response.headers["x-request-id"].startswith("req_")
    assert response.headers["x-tracker-id"].startswith("trace_")


def test_run_projection_is_resolved_through_configured_plugin(tmp_path: Path, monkeypatch) -> None:
    module = types.ModuleType("test_projection_plugin")

    class Provider:
        view_id = "demo.search"
        schema_version = 1

        def build(self, context):
            return {"view": self.view_id, "run_id": context.run.run_id, "user_id": context.user_id}

    class Plugin:
        plugin_id = "demo.projection"
        version = "1.0.0"

        def register(self, registry):
            registry.register_projection(Provider())

    module.create_plugin = lambda: Plugin()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    config = Config(tools={"capability_plugins": [module.__name__]})
    store = PostgresTestStore(tmp_path / "projection.db")
    store.create_api_access_token(user_id="user-a", actor_id="test", token="token-a")
    container = build_api_container(config=config, store=store)
    client = TestClient(create_app(container))
    headers = {"Authorization": "Bearer token-a"}
    with client:
        created = client.post("/v1/runs", headers=headers, json={
            "agent_id": "default", "session_id": "projection-session",
            "input": {"type": "message", "content": "hello"},
        })
        run_id = created.json()["run_id"]
        projected = client.get(f"/v1/runs/{run_id}/projection?view=demo.search", headers=headers)
        assert projected.status_code == 200
        assert projected.json() == {"view": "demo.search", "run_id": run_id, "user_id": "user-a"}
        unsupported = client.get(f"/v1/runs/{run_id}/projection?view=dinq.search", headers=headers)
        assert unsupported.status_code == 400


def test_run_generates_session_and_resumes_configured_clarification(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    store.publish_capability(
        CapabilityDefinition(
            ref=CapabilityRef("speech.synthesize", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test"),
            name="Speech synthesis",
            description="Generate audio",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            adapter="builtin.speech",
        )
    )
    store.save_scenario_version(
        ScenarioVersion(
            scenario_id="tts",
            version=1,
            name="TTS",
            description="",
            fields=(ScenarioField("voice", "string", required=True, enum=("pro", "default")),),
            nodes=(ClarificationNode("voice", "question", "选择声音", ("voice",)),),
            edges=(),
            allowed_capabilities=(CapabilityRef("speech.synthesize", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test"),),
            planning_mode="fixed",
            execution_policy={
                "aggregate": False,
                "tasks": [
                    {
                        "id": "synthesize",
                        "capability": CapabilityRef("speech.synthesize", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test").to_dict(),
                        "input": {"voice": "${voice}"},
                    }
                ],
            },
        ),
        status="published",
    )
    headers = {"Authorization": "Bearer token-a", "Idempotency-Key": "tts-one"}
    with client:
        created = client.post(
            "/v1/runs",
            headers=headers,
            json={
                "agent_id": "default",
                "scenario_id": "tts",
                "input": {"type": "message", "content": "生成语音"},
            },
        )
        assert created.status_code == 202
        body = created.json()
        assert body["session_id"].startswith("sess_")
        assert body["status"] == "waiting_input"
        run_id = body["run_id"]
        pending = client.get(f"/v1/runs/{run_id}/inputs/pending", headers=headers).json()["items"]
        assert pending[0]["question"] == "选择声音"

        resolved = client.post(
            f"/v1/runs/{run_id}/inputs",
            headers={**headers, "Idempotency-Key": "tts-answer"},
            json={"input_request_id": pending[0]["input_request_id"], "answers": {"voice": "pro"}},
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["run"]["run_id"] == run_id
        assert resolved.json()["run"]["status"] == "queued"
        assert resolved.json()["run"]["kind"] == "graph"
        assert len(store.list_runtime_tasks(run_id=run_id)) == 1
        assert resolved.json()["pending_inputs"] == []

        foreign = client.get(
            f"/v1/runs/{run_id}/inputs/pending",
            headers={"Authorization": "Bearer token-b"},
        )
        assert foreign.status_code == 404


def test_authentication_and_operator_impersonation(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    with client:
        assert client.get("/v1/me").status_code == 401
        assert (
            client.get("/v1/me", headers={"Authorization": "Bearer token-a"}).json()["user_id"]
            == "user-a"
        )

        monkeypatch.setenv("JOYHOUSEBOT_CONTROL_TOKEN", "operator-token")
        missing = client.get("/v1/runs", headers={"Authorization": "Bearer operator-token"})
        delegated = client.get(
            "/v1/runs",
            headers={
                "Authorization": "Bearer operator-token",
                "X-Impersonate-User-ID": "user-a",
            },
        )
        assert missing.status_code == 400
        assert delegated.status_code == 200


def test_health_and_readiness_do_not_require_user_auth(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        assert client.get("/healthz").json()["ok"] is True
        # /readyz only exposes a boolean; details require authentication.
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json() == {"ok": True}
        assert client.get("/v1/system/health").status_code == 401
        detailed = client.get("/v1/system/health", headers={"Authorization": "Bearer token-a"})
        assert detailed.status_code == 200
        assert detailed.json().get("ok") is True


def test_auth_fails_closed_when_no_tokens_configured(tmp_path: Path) -> None:
    """Empty token config must NOT silently trust X-User-Id (C1)."""
    config = Config()  # no database tokens/control token; allow_insecure_auth=False
    store = PostgresTestStore(tmp_path / "cloud.db")
    client = TestClient(create_app(build_api_container(config=config, store=store)))
    with client:
        assert client.get("/v1/me").status_code == 401
        assert client.get("/v1/me", headers={"X-User-Id": "mallory"}).status_code == 401


def test_insecure_dev_mode_requires_explicit_opt_in(tmp_path: Path) -> None:
    config = Config()
    config.gateway.allow_insecure_auth = True
    store = PostgresTestStore(tmp_path / "cloud.db")
    client = TestClient(create_app(build_api_container(config=config, store=store)))
    with client:
        response = client.get("/v1/me", headers={"X-User-Id": "local-user"})
        assert response.status_code == 200
        assert response.json()["user_id"] == "local-user"
        assert response.json()["subject"] == "dev:local-user"


def test_insecure_default_user_is_explicit_test_admin(tmp_path: Path) -> None:
    config = Config()
    config.gateway.allow_insecure_auth = True
    store = PostgresTestStore(tmp_path / "cloud.db")
    client = TestClient(create_app(build_api_container(config=config, store=store)))
    with client:
        identity = client.get("/v1/me", headers={"X-User-Id": "local-dev"})
        assert identity.status_code == 200
        assert identity.json()["role"] == "admin"
        assert identity.json()["is_admin"] is True
        admins = client.get("/v1/admin/users", headers={"X-User-Id": "local-dev"})
        assert admins.status_code == 200
        assert admins.json()["items"][0]["is_test_user"] is True

        ordinary = client.get("/v1/admin/overview", headers={"X-User-Id": "someone-else"})
        assert ordinary.status_code == 403


def test_database_admin_membership_grants_platform_api_without_changing_user_scope(
    tmp_path: Path,
) -> None:
    client, store = _client(tmp_path)
    store.upsert_platform_admin(
        user_id="user-a",
        role="admin",
        permissions=["*"],
        actor_id="test",
    )
    with client:
        created_a = client.post(
            "/v1/runs",
            headers={"Authorization": "Bearer token-a"},
            json={"input": {"type": "message", "content": "a"}},
        )
        created_b = client.post(
            "/v1/runs",
            headers={"Authorization": "Bearer token-b"},
            json={"input": {"type": "message", "content": "b"}},
        )
        assert created_a.status_code == created_b.status_code == 202

        platform = client.get("/v1/admin/runs", headers={"Authorization": "Bearer token-a"})
        assert {item["user_id"] for item in platform.json()["items"]} == {
            "user-a",
            "user-b",
        }
        assert platform.json()["pagination"] == {
            "page": 1,
            "limit": 10,
            "total": 2,
            "total_pages": 1,
        }
        assert "options" not in platform.json()["items"][0]
        assert "result" not in platform.json()["items"][0]
        ordinary = client.get("/v1/runs", headers={"Authorization": "Bearer token-a"})
        assert {item["user_id"] for item in ordinary.json()["items"]} == {"user-a"}

        granted = client.put(
            "/v1/admin/users/user-b",
            headers={"Authorization": "Bearer token-a"},
            json={"role": "viewer", "permissions": ["platform.read"]},
        )
        assert granted.status_code == 200
        assert granted.json()["role"] == "viewer"
        self_delete = client.delete(
            "/v1/admin/users/user-a", headers={"Authorization": "Bearer token-a"}
        )
        assert self_delete.status_code == 409


def test_control_plane_uses_operation_permissions_and_versioned_catalogs(
    tmp_path: Path,
) -> None:
    client, store = _client(tmp_path)
    store.upsert_platform_admin(
        user_id="user-a", role="admin", permissions=["*"], actor_id="test"
    )
    store.upsert_platform_admin(
        user_id="user-b",
        role="viewer",
        permissions=["runs.read"],
        actor_id="test",
    )
    admin_headers = {"Authorization": "Bearer token-a"}
    viewer_headers = {"Authorization": "Bearer token-b"}
    with client:
        created = client.post(
            "/v1/runs",
            headers=admin_headers,
            json={"input": {"type": "message", "content": "permission test"}},
        )
        run_id = created.json()["run_id"]
        assert client.get(f"/v1/admin/runs/{run_id}", headers=viewer_headers).status_code == 200
        assert (
            client.post(f"/v1/admin/runs/{run_id}/cancel", headers=viewer_headers).status_code
            == 403
        )

        catalog = client.get("/v1/admin/permissions", headers=admin_headers)
        assert catalog.status_code == 200
        assert "runs.cancel" in {
            item["permission"] for item in catalog.json()["items"]
        }
        config_summary = client.get("/v1/admin/config", headers=admin_headers)
        assert config_summary.status_code == 200
        assert config_summary.json()["providers"]["default_provider"] == {
            "configured": False,
            "endpoint": None,
        }
        mcp_saved = client.put(
            "/v1/admin/mcp-servers/test-filesystem",
            headers=admin_headers,
            json={
                "enabled": True,
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
                "env": {},
                "url": "",
            },
        )
        assert mcp_saved.status_code == 200
        assert client.get("/v1/admin/mcp-servers", headers=admin_headers).json()["items"][0]["name"] == "test-filesystem"
        mcp_test = client.post("/v1/admin/mcp-servers/test-filesystem/test", headers=admin_headers)
        assert mcp_test.status_code == 200 and mcp_test.json()["ok"]
        assert client.delete("/v1/admin/mcp-servers/test-filesystem", headers=admin_headers).status_code == 200
        draft = client.put(
            "/v1/admin/agents/research/revisions/research:v1",
            headers=admin_headers,
            json={
                "revision_id": "research:v1",
                "version": 1,
                "name": "Research",
                "role": "specialist",
                "instructions": "Use primary sources.",
                "model_policy": {"primary": "test/model"},
            },
        )
        assert draft.status_code == 200
        skill = client.put(
            "/v1/admin/capabilities/skill.research/versions/1.0.0",
            headers=admin_headers,
            json={
                "kind": "skill",
                "name": "Research Skill",
                "adapter": "prompt-skill:research",
            },
        )
        assert skill.status_code == 200
        bound = client.put(
            "/v1/admin/agents/research/revisions/research:v1/skills",
            headers=admin_headers,
            json={
                "skill_id": "skill.research",
                "skill_version": "1.0.0",
                "activation_mode": "always",
                "priority": 10,
            },
        )
        assert bound.status_code == 200
        bindings = client.get(
            "/v1/admin/agents/research/revisions/research:v1/skills",
            headers=admin_headers,
        )
        assert bindings.status_code == 200
        assert bindings.json()["items"] == [
            {
                "agent_revision_id": "research:v1",
                "skill_id": "skill.research",
                "skill_version": "1.0.0",
                "activation_mode": "always",
                "priority": 10,
                "configuration": {},
            }
        ]
        published = client.post(
            "/v1/admin/agents/research/revisions/research:v1/publish",
            headers=admin_headers,
        )
        assert published.status_code == 200
        agents = client.get("/v1/admin/agents", headers=admin_headers).json()["items"]
        assert any(item["agent_id"] == "research" for item in agents)
        rollouts = client.get("/v1/admin/rollouts", headers=admin_headers).json()["items"]
        assert rollouts[0]["status"] == "completed"

        issued = client.post(
            "/v1/admin/access-tokens",
            headers=admin_headers,
            json={"user_id": "user-c", "label": "integration-test"},
        )
        assert issued.status_code == 201
        plaintext = issued.json()["token"]
        token_id = issued.json()["token_id"]
        listed_tokens = client.get(
            "/v1/admin/access-tokens", headers=admin_headers
        ).json()["items"]
        assert all("token" not in item for item in listed_tokens)
        assert (
            client.get(
                "/v1/me", headers={"Authorization": f"Bearer {plaintext}"}
            ).json()["user_id"]
            == "user-c"
        )
        assert (
            client.delete(
                f"/v1/admin/access-tokens/{token_id}", headers=admin_headers
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/v1/me", headers={"Authorization": f"Bearer {plaintext}"}
            ).status_code
            == 401
        )


def test_admin_model_diagnostics_raw_payload_and_offline_replay(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    store.upsert_platform_admin(
        user_id="user-a",
        role="admin",
        permissions=["*"],
        actor_id="test",
    )
    store.upsert_platform_admin(
        user_id="user-b",
        role="viewer",
        permissions=["platform.read", "runs.read"],
        actor_id="test",
    )
    with client:
        created = client.post(
            "/v1/runs",
            headers={"Authorization": "Bearer token-a"},
            json={"input": {"type": "message", "content": "trace me"}},
        )
        run_id = created.json()["run_id"]
        blob = store.put_trace_blob(
            run_id=run_id,
            kind="model.request",
            content={"messages": [{"role": "user", "content": "trace me"}]},
        )
        span = store.start_execution_span(
            span_id="span-api-model",
            trace_id="trace-api",
            run_id=run_id,
            span_kind="model",
            name="test:model",
        )
        invocation = store.create_model_invocation(
            invocation_id="model-api",
            run_id=run_id,
            span_id=span.span_id,
            provider="test",
            model="test/model",
            operation="chat",
            request_blob_id=blob.blob_id,
        )
        store.append_reasoning_segment(
            invocation_id=invocation.invocation_id,
            run_id=run_id,
            source="provider_native",
            content="private diagnostic reasoning",
        )

        headers = {"Authorization": "Bearer token-a"}
        diagnostics = client.get(f"/v1/admin/runs/{run_id}/diagnostics", headers=headers)
        assert diagnostics.status_code == 200
        assert diagnostics.json()["model_invocations"][0]["invocation_id"] == "model-api"
        assert diagnostics.json()["reasoning"][0]["fidelity"] == "exact"

        raw = client.get(f"/v1/admin/runs/{run_id}/blobs/{blob.blob_id}", headers=headers)
        assert raw.status_code == 200
        assert raw.json()["content"]["messages"][0]["content"] == "trace me"

        replay = client.post(
            f"/v1/admin/runs/{run_id}/replays",
            headers=headers,
            json={"mode": "offline"},
        )
        assert replay.status_code == 202
        assert replay.json()["comparison"]["content_equal"] is True

        viewer_headers = {"Authorization": "Bearer token-b"}
        viewer_diagnostics = client.get(
            f"/v1/admin/runs/{run_id}/diagnostics", headers=viewer_headers
        )
        assert viewer_diagnostics.status_code == 200
        assert viewer_diagnostics.json()["reasoning"] == []
        assert (
            client.get(f"/v1/admin/runs/{run_id}/reasoning", headers=viewer_headers).status_code
            == 403
        )
        assert (
            client.get(
                f"/v1/admin/runs/{run_id}/blobs/{blob.blob_id}", headers=viewer_headers
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/v1/admin/runs/{run_id}/replays",
                headers=viewer_headers,
                json={"mode": "offline"},
            ).status_code
            == 403
        )


def test_rate_limit_returns_429(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JOYHOUSEBOT_API_RATE_PER_MINUTE", "5")
    client, _ = _client(tmp_path)
    with client:
        codes = [
            client.get("/v1/me", headers={"Authorization": "Bearer token-a"}).status_code
            for _ in range(6)
        ]
        assert codes[:5] == [200] * 5
        assert codes[5] == 429


def test_rate_limit_counts_failed_auth_per_client_ip(tmp_path: Path, monkeypatch) -> None:
    """Rotating bearer tokens must not reset a brute-force budget (H2)."""
    monkeypatch.setenv("JOYHOUSEBOT_API_RATE_PER_MINUTE", "5")
    client, _ = _client(tmp_path)
    with client:
        codes = [
            client.get("/v1/me", headers={"Authorization": f"Bearer wrong-{i}"}).status_code
            for i in range(8)
        ]
        assert 401 in codes
        assert 429 in codes


def test_cors_uses_configured_origins(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        allowed = client.options(
            "/v1/me",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
        rejected = client.options(
            "/v1/me",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" not in rejected.headers


def test_run_ids_are_format_validated(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        response = client.post(
            "/v1/runs",
            headers={"Authorization": "Bearer token-a"},
            json={
                "agent_id": "bad agent!",
                "session_id": "main",
                "input": {"type": "message", "content": "hello"},
            },
        )
        assert response.status_code == 422


def test_delete_session_with_active_run_returns_409(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        created = client.post(
            "/v1/runs",
            headers={"Authorization": "Bearer token-a"},
            json={
                "agent_id": "default",
                "session_id": "main",
                "input": {"type": "message", "content": "hello"},
            },
        )
        assert created.status_code == 202
        agent_id = created.json()["agent_id"]
        response = client.delete(
            f"/v1/sessions/{agent_id}/main", headers={"Authorization": "Bearer token-a"}
        )
        assert response.status_code == 409


def test_schedule_delivery_requires_enabled_channel(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    schedule = {
        "name": "daily-review",
        "agent_id": "joy",
        "schedule": {"kind": "every", "every_ms": 60_000},
        "payload": {"message": "review", "deliver": True, "channel": "telegram", "to": "12345"},
    }
    with client:
        # No channels enabled in config -> delivery must be rejected.
        rejected = client.post(
            "/v1/schedules", headers={"Authorization": "Bearer token-a"}, json=schedule
        )
        assert rejected.status_code == 422

        config = client.app.state.container.config
        config.channels.telegram.enabled = True

        # Unknown (not enabled) channel stays rejected.
        unknown = dict(schedule)
        unknown["payload"] = {**schedule["payload"], "channel": "whatsapp"}
        assert (
            client.post(
                "/v1/schedules", headers={"Authorization": "Bearer token-a"}, json=unknown
            ).status_code
            == 422
        )

        # Unsafe delivery target stays rejected.
        unsafe = dict(schedule)
        unsafe["payload"] = {**schedule["payload"], "to": "bad target!!"}
        assert (
            client.post(
                "/v1/schedules", headers={"Authorization": "Bearer token-a"}, json=unsafe
            ).status_code
            == 422
        )

        # Enabled channel + safe target is accepted.
        accepted = client.post(
            "/v1/schedules", headers={"Authorization": "Bearer token-a"}, json=schedule
        )
        assert accepted.status_code == 201


def test_schedules_are_user_scoped(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    schedule = {
        "name": "daily-review",
        "agent_id": "joy",
        "schedule": {"kind": "every", "every_ms": 60_000},
        "payload": {"message": "review"},
    }
    with client:
        created = client.post(
            "/v1/schedules",
            headers={"Authorization": "Bearer token-a"},
            json=schedule,
        )
        assert created.status_code == 201
        schedule_id = created.json()["id"]
        own = client.get("/v1/schedules", headers={"Authorization": "Bearer token-a"})
        other = client.get("/v1/schedules", headers={"Authorization": "Bearer token-b"})
        assert [item["id"] for item in own.json()["items"]] == [schedule_id]
        assert other.json()["items"] == []
        updated = client.patch(
            f"/v1/schedules/{schedule_id}",
            headers={"Authorization": "Bearer token-a"},
            json={"name": "weekly-review", "enabled": False},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "weekly-review"
        assert updated.json()["enabled"] is False
        assert (
            client.delete(
                f"/v1/schedules/{schedule_id}",
                headers={"Authorization": "Bearer token-b"},
            ).status_code
            == 404
        )
