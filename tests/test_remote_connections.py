from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from joyhousebot_connector_http_capability import (
    HTTP_CAPABILITY_CONNECTOR_MANIFEST,
    create_extension,
)

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.agent_runtime_catalog import AgentRuntimeCatalog
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.connectors import CapabilityConnectorRegistry
from joyhousebot.domain.remote_connections import (
    materialize_remote_connection,
    normalize_remote_connection,
)
from tests.support.postgres_store import PostgresTestStore


def _capability(version: str = "1.0.0") -> dict:
    return {
        "capability_id": "crm.lead.read",
        "version": version,
        "implementation_digest": f"sha256:{'1' * 64}",
        "name": "Read lead",
        "description": "Read one lead",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object"},
        "permissions": ["crm.lead.read"],
        "side_effect": "read",
        "idempotent": True,
        "data_classification": "confidential",
    }


def _configuration(*, capability_version: str = "1.0.0") -> dict:
    return {
        "service_profile": "business",
        "enabled": True,
        "base_url": "https://crm.example.test/joyhousebot/v1",
        "key_id": "crm-key-1",
        "signing_secret_ref": "env://CRM_REMOTE_TEST_SECRET",
        "require_response_signature": True,
        "timeout_seconds": 60,
        "max_response_bytes": 1024 * 1024,
        "capabilities": [_capability(capability_version)],
    }


def _host_configuration() -> dict:
    return {
        **_configuration(),
        "service_profile": "extension_host",
        "host_protocol_version": "1",
        "expected_host_manifest_digest": f"sha256:{'8' * 64}",
        "require_host_preflight": True,
    }


def _store(tmp_path: Path) -> PostgresTestStore:
    return PostgresTestStore(tmp_path / "remote-connections.db")


def _activate_connector(store: PostgresTestStore, worker_id: str = "agent-remote-a") -> None:
    release = HTTP_CAPABILITY_CONNECTOR_MANIFEST.to_release_dict()
    store.upsert_extension_release(release)
    store.register_runtime_worker(
        worker_id=worker_id,
        capabilities={"agent": True},
        metadata={"extensions": [release]},
    )
    store.stage_extension_release(
        release["extension_id"], release["version"], actor_id="test"
    )
    assert store.acknowledge_configuration_revision(
        worker_id=worker_id,
        aggregate_type="extension",
        aggregate_id=release["extension_id"],
        revision_id=release["version"],
    )


def test_remote_connection_rejects_plaintext_and_materializes_only_in_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="env://VARIABLE"):
        normalize_remote_connection(
            "crm", {**_configuration(), "signing_secret_ref": "plaintext-secret"}
        )
    normalized = normalize_remote_connection("crm", _configuration())
    assert normalized["signing_secret_ref"] == "env://CRM_REMOTE_TEST_SECRET"
    monkeypatch.setenv("CRM_REMOTE_TEST_SECRET", "s" * 32)
    materialized = materialize_remote_connection(normalized)
    assert materialized["signing_secret"] == "s" * 32
    assert "signing_secret_ref" not in materialized
    with pytest.raises(ValueError, match="preflight cannot be disabled"):
        normalize_remote_connection(
            "node-host", {**_host_configuration(), "require_host_preflight": False}
        )


def test_remote_connection_rollout_and_safe_rollback(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _activate_connector(store)
    first = store.save_remote_connection_revision(
        "crm",
        name="CRM",
        description="Sales service",
        configuration=_configuration(),
        actor_id="admin",
    )
    assert first["revision_id"] == "crm:v1"
    assert first["configuration"]["signing_secret_ref"] == "env://CRM_REMOTE_TEST_SECRET"
    assert "signing_secret" not in first["configuration"]
    first_rollout = store.stage_remote_connection_revision(
        "crm", "crm:v1", actor_id="admin"
    )
    assert store.acknowledge_configuration_revision(
        worker_id="agent-remote-a",
        aggregate_type="remote_connection",
        aggregate_id="crm",
        revision_id="crm:v1",
    )
    assert store.get_configuration_rollout(first_rollout).status == "completed"
    assert store.get_remote_connection("crm")["current_revision_id"] == "crm:v1"

    second = store.save_remote_connection_revision(
        "crm",
        name="CRM",
        description="Sales service",
        configuration=_configuration(capability_version="2.0.0"),
        actor_id="admin",
    )
    second_rollout = store.stage_remote_connection_revision(
        "crm", second["revision_id"], actor_id="admin"
    )
    assert store.acknowledge_configuration_revision(
        worker_id="agent-remote-a",
        aggregate_type="remote_connection",
        aggregate_id="crm",
        revision_id="crm:v2",
    )
    assert store.get_remote_connection("crm")["current_revision_id"] == "crm:v2"
    assert store.rollback_configuration_rollout(second_rollout, actor_id="operator")
    assert store.acknowledge_configuration_revision(
        worker_id="agent-remote-a",
        aggregate_type="remote_connection",
        aggregate_id="crm",
        revision_id="crm:v1",
    )
    assert store.get_remote_connection("crm")["current_revision_id"] == "crm:v1"
    assert store.get_configuration_rollout(second_rollout).status == "rolled_back"


@pytest.mark.asyncio
async def test_worker_preflight_discovers_exact_remote_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.save_remote_connection_revision(
        "crm",
        name="CRM",
        description="Sales service",
        configuration=_configuration(),
        actor_id="admin",
    )
    connectors = CapabilityConnectorRegistry()
    connectors.register(create_extension(), source="test")
    loop = SimpleNamespace(capability_connectors=connectors)
    catalog = AgentRuntimeCatalog(config=Config(), store=store)
    catalog._runtime = SimpleNamespace(  # noqa: SLF001
        default_agent_id="default", worker_id="agent-remote-a"
    )
    catalog.resolve = lambda _key: loop  # type: ignore[method-assign]
    monkeypatch.setenv("CRM_REMOTE_TEST_SECRET", "s" * 32)

    await catalog._preheat_remote_connection(  # noqa: SLF001
        {
            "aggregate_type": "remote_connection",
            "aggregate_id": "crm",
            "revision_id": "crm:v1",
        }
    )

    discovered = store.get_capability_release_definition("crm.lead.read", "1.0.0")
    assert discovered is not None
    assert discovered["ref"]["extension_id"] == "connector-http-capability"
    assert discovered["origin"]["remote_service_id"] == "crm"


@pytest.mark.asyncio
async def test_worker_runs_extension_host_preflight_before_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.save_remote_connection_revision(
        "node-host",
        name="Node Host",
        description="Managed extension host",
        configuration=_host_configuration(),
        actor_id="admin",
    )
    calls: list[dict] = []

    async def preflight(settings: dict) -> dict:
        calls.append(settings)
        return {"manifest_digest": f"sha256:{'8' * 64}"}

    declared = create_extension()
    extension = type(declared)(
        manifest=declared.manifest,
        connect=declared.connect,
        preflight=preflight,
    )
    connectors = CapabilityConnectorRegistry()
    connectors.register(extension, source="test")
    catalog = AgentRuntimeCatalog(config=Config(), store=store)
    catalog._runtime = SimpleNamespace(  # noqa: SLF001
        default_agent_id="default", worker_id="agent-remote-a"
    )
    catalog.resolve = lambda _key: SimpleNamespace(  # type: ignore[method-assign]
        capability_connectors=connectors
    )
    monkeypatch.setenv("CRM_REMOTE_TEST_SECRET", "s" * 32)

    await catalog._preheat_remote_connection(  # noqa: SLF001
        {
            "aggregate_type": "remote_connection",
            "aggregate_id": "node-host",
            "revision_id": "node-host:v1",
        }
    )

    assert len(calls) == 1
    assert calls[0]["service_id"] == "node-host"
    assert calls[0]["service"]["signing_secret"] == "s" * 32
    assert "signing_secret_ref" not in calls[0]["service"]


@pytest.mark.asyncio
async def test_capability_preheat_syncs_active_remote_connection_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = {
        "aggregate_type": "capability",
        "aggregate_id": "crm.lead.read",
        "revision_id": "crm.lead.read:1.0.0",
    }
    store = SimpleNamespace(
        list_pending_configuration_revisions=lambda _worker_id: [item]
    )
    catalog = AgentRuntimeCatalog(config=Config(), store=store)
    catalog._runtime = SimpleNamespace(worker_id="agent-remote-a")  # noqa: SLF001
    events: list[str] = []

    async def refresh_remote_connections() -> None:
        events.append("refresh-remote-connections")

    async def refresh_model_providers() -> None:
        events.append("refresh-model-providers")

    def preheat_configuration(_item: dict[str, str]) -> None:
        events.append("preheat-capability")

    def acknowledge_configuration(
        _item: dict[str, str], *, status: str, error: dict | None = None
    ) -> None:
        assert error is None
        events.append(f"ack-{status}")

    monkeypatch.setattr(
        catalog, "_refresh_active_remote_connections", refresh_remote_connections
    )
    monkeypatch.setattr(
        catalog, "_refresh_active_model_providers", refresh_model_providers
    )
    monkeypatch.setattr(catalog, "_preheat_configuration", preheat_configuration)
    monkeypatch.setattr(
        catalog, "_acknowledge_configuration", acknowledge_configuration
    )

    assert await catalog.refresh_pending() == 1
    assert events[:3] == [
        "refresh-remote-connections",
        "preheat-capability",
        "ack-loaded",
    ]


def test_remote_connection_control_api_never_returns_secret_values(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_operator_access_token(
        user_id="operator", actor_id="test", token="remote-connection-token"
    )
    store.upsert_platform_admin(
        user_id="operator", permissions=["*"], actor_id="test"
    )
    body = {
        "connection_id": "crm",
        "name": "CRM",
        "description": "Sales service",
        **_configuration(),
    }
    container = build_api_container(config=Config(), store=store)
    headers = {"Authorization": "Bearer remote-connection-token"}
    with TestClient(create_app(container)) as client:
        created = client.post(
            "/control/v1/admin/remote-connections", headers=headers, json=body
        )
        assert created.status_code == 201
        listed = client.get("/control/v1/admin/remote-connections", headers=headers)
        assert listed.status_code == 200
        payload = listed.json()["items"][0]
        serialized = str(payload)
        assert "env://CRM_REMOTE_TEST_SECRET" in serialized
        assert "signing_secret'" not in serialized
        rejected = client.post(
            "/control/v1/admin/remote-connections",
            headers=headers,
            json={**body, "connection_id": "bad", "signing_secret_ref": "plaintext"},
        )
        assert rejected.status_code == 422
