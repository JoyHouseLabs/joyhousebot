from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.application.plugins import run_plugin_diagnostics
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config, ToolsConfig
from joyhousebot.contracts.plugins import PluginComponent, PluginManifest
from joyhousebot.domain.capabilities import CapabilityDefinition, CapabilityKind, CapabilityRef
from tests.support.postgres_store import PostgresTestStore


def _store(tmp_path: Path) -> PostgresTestStore:
    store = PostgresTestStore(tmp_path / "plugin-control.db")
    manifest = PluginManifest(
        plugin_id="example.discover",
        version="1.0.0",
        name="Example Discover",
        distribution_name="example-plugin",
        build_digest="sha256:test-example-discover",
    )
    store.upsert_plugin_release(manifest.to_dict())
    store.sync_plugin_components(
        manifest.plugin_id,
        manifest.version,
        [
            PluginComponent(
                component_id="example.search",
                component_type="tool",
                name="Example search",
                reference_id="example.search",
                reference_version="1.0.0",
            ).to_dict(),
            PluginComponent(
                component_id="skill.example.search",
                component_type="skill",
                name="Search skill",
                reference_id="skill.example.search",
                reference_version="1.0.0",
            ).to_dict(),
        ],
    )
    return store


def test_plugin_catalog_is_durable_and_metrics_are_empty_without_invocations(tmp_path: Path) -> None:
    store = _store(tmp_path)
    release = store.get_plugin_release("example.discover")
    assert release and release["name"] == "Example Discover"
    assert [item["component_id"] for item in store.list_plugin_components("example.discover")] == [
        "skill.example.search",
        "example.search",
    ]
    metrics = store.get_plugin_metrics("example.discover")
    assert metrics["total"] == 0
    assert metrics["by_component"] == []


def test_plugin_release_digest_cannot_be_overwritten(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="immutable"):
        store.upsert_plugin_release(
            PluginManifest(
                plugin_id="example.discover",
                version="1.0.0",
                name="Example Discover",
                build_digest="sha256:different-build",
            ).to_dict()
        )


def test_plugin_control_plane_api_requires_admin_and_projects_safe_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_api_access_token(user_id="operator", actor_id="test", token="plugin-token")
    container = build_api_container(config=Config(), store=store)
    client = TestClient(create_app(container))
    with client:
        denied = client.get("/v1/admin/plugins", headers={"Authorization": "Bearer plugin-token"})
        assert denied.status_code == 403
        store.upsert_platform_admin(user_id="operator", permissions=["*"], actor_id="test")
        listed = client.get("/v1/admin/plugins", headers={"Authorization": "Bearer plugin-token"})
        assert listed.status_code == 200
        assert listed.json()["items"][0]["plugin_id"] == "example.discover"
        detail = client.get(
            "/v1/admin/plugins/example.discover", headers={"Authorization": "Bearer plugin-token"}
        )
        assert detail.status_code == 200
        assert detail.json()["components"][0]["metadata"] == {}


def test_capability_runtime_settings_api_requires_publish_permission(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.publish_capability(
        CapabilityDefinition(
            ref=CapabilityRef("example.search", "1.0.0", CapabilityKind.TOOL, "test.plugin", "1.0.0", "sha256:test"),
            name="Example search", description="", input_schema={"type": "object"}, output_schema={"type": "object"}, adapter="example.search",
            configuration_schema={"type": "object", "additionalProperties": False, "properties": {"limit": {"type": "integer"}}},
        )
    )
    store.create_api_access_token(user_id="operator", actor_id="test", token="runtime-settings-token")
    container = build_api_container(config=Config(), store=store)
    client = TestClient(create_app(container))
    headers = {"Authorization": "Bearer runtime-settings-token"}
    with client:
        assert client.put("/v1/admin/capabilities/example.search/runtime-settings", headers=headers, json={"enabled": False, "configuration": {"limit": 5}}).status_code == 403
        store.upsert_platform_admin(user_id="root-admin", permissions=["*"], actor_id="bootstrap")
        store.upsert_platform_admin(user_id="operator", permissions=["capabilities.read", "capabilities.publish"], actor_id="test")
        response = client.put("/v1/admin/capabilities/example.search/runtime-settings", headers=headers, json={"enabled": False, "configuration": {"limit": 5}})
        assert response.status_code == 200
        assert response.json()["enabled"] is False
        invalid = client.put("/v1/admin/capabilities/example.search/runtime-settings", headers=headers, json={"enabled": True, "configuration": {"limit": "five"}})
        assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_declared_plugin_diagnostics_are_persisted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    config = Config(tools=ToolsConfig(capability_plugins=["dinq_plugin.discover.plugin"]))
    # The Dinq catalog is deliberately absent here: the diagnostic must report
    # that fact as a safe failed result rather than raising or accessing a user.
    results = await run_plugin_diagnostics(config=config, store=store, plugin_id="dinq.discover")
    assert {item["name"] for item in results} == {
        "catalog",
        "worker_release",
        "connections",
    }
    persisted = store.list_plugin_check_results("dinq.discover")
    assert {item["name"] for item in persisted} == {
        "catalog",
        "worker_release",
        "connections",
    }
