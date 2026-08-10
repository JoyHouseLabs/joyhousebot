from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.bootstrap.extension_rollouts import ExtensionRolloutWatcher
from joyhousebot.config.schema import Config
from joyhousebot.contracts.extensions import ExtensionManifest
from joyhousebot.contracts.plugins import PluginComponent, PluginManifest, PluginQuickstart
from joyhousebot.domain.capabilities import CapabilityDefinition, CapabilityKind, CapabilityRef
from tests.support.postgres_store import PostgresTestStore

TEST_BUILD_DIGEST = f"sha256:{'a' * 64}"
OTHER_BUILD_DIGEST = f"sha256:{'b' * 64}"


def _extension_manifest(extension_id: str, extension_type: str) -> ExtensionManifest:
    return ExtensionManifest(
        extension_id=extension_id,
        version="1.0.0",
        name=extension_id,
        extension_types=(extension_type,),
        build_digest=TEST_BUILD_DIGEST,
    )


def _store(tmp_path: Path) -> PostgresTestStore:
    store = PostgresTestStore(tmp_path / "plugin-control.db")
    manifest = PluginManifest(
        plugin_id="example.discover",
        version="1.0.0",
        name="Example Discover",
        distribution_name="example-plugin",
        build_digest=TEST_BUILD_DIGEST,
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
    assert release["status"] == "discovered"
    assert [item["component_id"] for item in store.list_plugin_components("example.discover")] == [
        "skill.example.search",
        "example.search",
    ]
    metrics = store.get_plugin_metrics("example.discover")
    assert metrics["total"] == 0
    assert metrics["by_component"] == []


def test_plugin_release_activates_only_after_exact_worker_load_ack(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_runtime_worker(
        worker_id="agent-plugin-a",
        capabilities={"agent": True},
        metadata={
            "extensions": [
                {
                    "plugin_id": "example.discover",
                    "version": "1.0.0",
                    "build_digest": TEST_BUILD_DIGEST,
                }
            ]
        },
    )

    rollout_id = store.stage_plugin_release(
        "example.discover", "1.0.0", actor_id="release-admin"
    )
    assert store.get_plugin_release("example.discover", "1.0.0")["status"] == "staged"
    assert store.get_active_plugin_release("example.discover") is None
    assert store.acknowledge_configuration_revision(
        worker_id="agent-plugin-a",
        aggregate_type="plugin",
        aggregate_id="example.discover",
        revision_id="1.0.0",
    )

    assert store.get_configuration_rollout(rollout_id).status == "completed"
    assert store.get_active_plugin_release("example.discover")["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_channel_extension_rollout_targets_and_is_acked_by_channel_worker(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    manifest = _extension_manifest("channel-example", "channel")
    release = manifest.to_release_dict()
    store.upsert_plugin_release(release)
    store.register_runtime_worker(
        worker_id="channel-worker-a",
        capabilities={"channels": True},
        metadata={"extensions": [release]},
    )
    store.register_runtime_worker(
        worker_id="agent-worker-a",
        capabilities={"agent": True},
        metadata={"extensions": []},
    )

    rollout_id = store.stage_plugin_release(
        manifest.extension_id,
        manifest.version,
        actor_id="release-admin",
    )
    assert [
        item["worker_id"]
        for item in store.list_configuration_rollout_targets(rollout_id)
    ] == ["channel-worker-a"]

    runtime = type(
        "Runtime",
        (),
        {"worker_id": "channel-worker-a", "plugin_releases": [release]},
    )()
    assert await ExtensionRolloutWatcher(store=store, runtime=runtime).refresh_pending() == 1
    assert store.get_active_plugin_release(manifest.extension_id)["version"] == "1.0.0"


def test_provider_extension_rollout_targets_agent_workers(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _extension_manifest("provider-example", "model_provider")
    store.upsert_plugin_release(manifest.to_release_dict())
    store.register_runtime_worker(
        worker_id="channel-worker-a",
        capabilities={"channels": True},
        metadata={"extensions": []},
    )
    store.register_runtime_worker(
        worker_id="agent-worker-a",
        capabilities={"agent": True},
        metadata={"extensions": [manifest.to_release_dict()]},
    )

    rollout_id = store.stage_plugin_release(
        manifest.extension_id,
        manifest.version,
        actor_id="release-admin",
    )
    assert [
        item["worker_id"]
        for item in store.list_configuration_rollout_targets(rollout_id)
    ] == ["agent-worker-a"]


def test_plugin_release_digest_cannot_be_overwritten(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="immutable"):
        store.upsert_plugin_release(
            PluginManifest(
                plugin_id="example.discover",
                version="1.0.0",
                name="Example Discover",
                build_digest=OTHER_BUILD_DIGEST,
            ).to_dict()
        )


def test_plugin_manifest_and_component_catalog_are_immutable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="manifest is immutable"):
        store.upsert_plugin_release(
            PluginManifest(
                plugin_id="example.discover",
                version="1.0.0",
                name="Renamed release",
                build_digest=TEST_BUILD_DIGEST,
            ).to_dict()
        )
    with pytest.raises(ValueError, match="component is immutable"):
        store.sync_plugin_components(
            "example.discover",
            "1.0.0",
            [
                PluginComponent(
                    component_id="example.search",
                    component_type="tool",
                    name="Mutated search",
                    reference_id="example.search",
                    reference_version="1.0.0",
                ).to_dict()
            ],
        )


def test_plugin_manifest_projects_business_owned_quickstarts() -> None:
    manifest = PluginManifest(
        plugin_id="example.discover",
        version="1.0.0",
        name="Example Discover",
        build_digest=TEST_BUILD_DIGEST,
        quickstarts=(
            PluginQuickstart(
                quickstart_id="catalog-search",
                title="Search the catalog",
                description="Use the coordinator rather than calling a hidden tool.",
                prompt="Find reinforcement learning engineers.",
                scenario_id="example.catalog.search",
                scenario_inputs={"query": "reinforcement learning"},
                capability_ids=("example.search",),
                required_connection_ids=("example-catalog",),
            ),
        ),
    )
    value = manifest.to_dict()
    assert value["quickstarts"] == [
        {
            "quickstart_id": "catalog-search",
            "title": "Search the catalog",
            "description": "Use the coordinator rather than calling a hidden tool.",
            "prompt": "Find reinforcement learning engineers.",
            "agent_id": "default",
            "scenario_id": "example.catalog.search",
            "scenario_inputs": {"query": "reinforcement learning"},
            "capability_ids": ["example.search"],
            "required_connection_ids": ["example-catalog"],
            "expected_outcome": "",
        }
    ]


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


def test_plugin_playground_creates_a_direct_durable_tool_run(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.publish_capability(
        CapabilityDefinition(
            ref=CapabilityRef(
                "example.search", "1.0.0", CapabilityKind.TOOL,
                "example.discover", "1.0.0", "sha256:test-example-discover",
            ),
            name="Example search",
            description="A safe test capability",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            output_schema={"type": "object"},
            adapter="example.search",
            side_effect="none",
        )
    )
    store.create_api_access_token(user_id="operator", actor_id="test", token="playground-token")
    store.upsert_platform_admin(user_id="operator", permissions=["*"], actor_id="test")
    container = build_api_container(config=Config(), store=store)
    client = TestClient(create_app(container))
    with client:
        response = client.post(
            "/v1/admin/plugins/example.discover/playground/runs",
            headers={"Authorization": "Bearer playground-token"},
            json={"capability_id": "example.search", "input": {"query": "Ada"}},
        )
    assert response.status_code == 202
    body = response.json()
    tasks = store.list_runtime_tasks(run_id=body["run_id"], limit=10)
    assert body["prompt"] == "Tool Playground: example.search"
    assert len(tasks) == 1
    assert tasks[0].payload["capability"]["capability_id"] == "example.search"
    assert tasks[0].payload["capability_input"] == {"query": "Ada"}
    assert store.get_runtime_run(body["run_id"]).options["aggregate"] is False
