from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.bootstrap.extension_catalog import discover_allowed_extensions
from joyhousebot.bootstrap.extension_rollouts import ExtensionRolloutWatcher
from joyhousebot.config.schema import Config, ExtensionsConfig
from joyhousebot.contracts.capability_extensions import CapabilityExtensionManifest
from joyhousebot.contracts.extensions import ExtensionManifest
from joyhousebot.domain.capabilities import CapabilityDefinition, CapabilityKind, CapabilityRef
from joyhousebot.extension_discovery import scan_extension_catalog
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
    store = PostgresTestStore(tmp_path / "extension-control.db")
    manifest = CapabilityExtensionManifest(
        extension_id="example.discover",
        version="1.0.0",
        name="Example Discover",
        distribution_name="example-extension",
        build_digest=TEST_BUILD_DIGEST,
    )
    store.upsert_extension_release(manifest.to_release_dict())
    store.sync_extension_components(
        manifest.extension_id,
        manifest.version,
        [
            {
                "component_id": "capability:example.search",
                "component_type": "capability",
                "name": "Example search",
                "reference_id": "example.search",
                "reference_version": "1.0.0",
                "metadata": {},
            },
        ],
    )
    return store


def test_extension_directory_scan_reads_metadata_without_importing_code(
    tmp_path: Path,
) -> None:
    extension = tmp_path / "extensions" / "capability-safe-scan"
    extension.mkdir(parents=True)
    (extension / "pyproject.toml").write_text(
        """[project]
name = "joyhousebot-capability-safe-scan"
version = "2.1.0"
description = "Metadata only"

[project.entry-points."joyhousebot.capabilities"]
capability-safe-scan = "module_that_must_never_import:create_extension"
""",
        encoding="utf-8",
    )

    values = scan_extension_catalog([tmp_path / "extensions"], installed=[])

    assert len(values) == 1
    assert values[0].extension_id == "capability-safe-scan"
    assert values[0].source_version == "2.1.0"
    assert values[0].installed is False
    assert values[0].metadata["entry_points"] == {
        "joyhousebot.capabilities": "module_that_must_never_import:create_extension"
    }


def test_extension_desired_state_survives_catalog_rescan_and_allowlist_closes_execution(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = {
        "extension_id": "example.discover",
        "name": "Example Discover",
        "description": "",
        "source_version": "1.0.0",
        "extension_types": ["capability"],
        "distribution_name": "example-extension",
        "distribution_version": "1.0.0",
        "source_location": str(tmp_path / "extensions" / "example.discover"),
        "source_digest": TEST_BUILD_DIGEST,
        "source_available": True,
        "installed": True,
        "metadata": {},
    }
    store.sync_extension_inventory(
        [candidate], allowed_ids={"example.discover"}, initially_active_ids=set()
    )
    assert store.is_extension_execution_enabled("example.discover") is False

    store.set_extension_desired_active(
        "example.discover", True, actor_id="release-admin"
    )
    store.sync_extension_inventory(
        [candidate], allowed_ids={"example.discover"}, initially_active_ids=set()
    )
    assert store.get_extension_inventory("example.discover")["desired_active"] is True
    assert store.is_extension_execution_enabled("example.discover") is True

    store.sync_extension_inventory(
        [candidate], allowed_ids=set(), initially_active_ids=set()
    )
    assert store.get_extension_inventory("example.discover")["desired_active"] is True
    assert store.is_extension_execution_enabled("example.discover") is False


def test_deactivated_extension_is_removed_from_active_capability_catalog(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    definition = CapabilityDefinition(
        ref=CapabilityRef(
            "example.search",
            "1.0.0",
            CapabilityKind.CAPABILITY,
            "example.discover",
            "1.0.0",
            TEST_BUILD_DIGEST,
        ),
        name="Example search",
        description="",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        adapter="example.search",
    )
    store.publish_capability(definition)
    store.sync_extension_inventory(
        [
            {
                "extension_id": "example.discover",
                "name": "Example Discover",
                "extension_types": ["capability"],
                "source_available": True,
                "installed": True,
                "metadata": {},
            }
        ],
        allowed_ids={"example.discover"},
        initially_active_ids={"example.discover"},
    )
    assert store.get_capability_definition("example.search") is not None

    store.set_extension_desired_active(
        "example.discover", False, actor_id="release-admin"
    )

    assert store.get_capability_definition("example.search") is None
    assert all(
        item["ref"]["capability_id"] != "example.search"
        for item in store.list_capability_definitions()
    )
    assert (
        store.get_capability_release_definition("example.search", "1.0.0")
        is not None
    )


def test_extension_catalog_is_durable_and_metrics_are_empty_without_invocations(tmp_path: Path) -> None:
    store = _store(tmp_path)
    release = store.get_extension_release("example.discover")
    assert release and release["name"] == "Example Discover"
    assert release["status"] == "discovered"
    assert [item["component_id"] for item in store.list_extension_components("example.discover")] == [
        "capability:example.search",
    ]
    metrics = store.get_extension_metrics("example.discover")
    assert metrics["total"] == 0
    assert metrics["by_component"] == []


def test_extension_components_can_be_resolved_for_an_upgrade_target(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    second = CapabilityExtensionManifest(
        extension_id="example.discover",
        version="2.0.0",
        name="Example Discover",
        distribution_name="example-extension",
        build_digest=OTHER_BUILD_DIGEST,
    )
    store.upsert_extension_release(second.to_release_dict())
    store.sync_extension_components(
        second.extension_id,
        second.version,
        [
            {
                "component_id": "capability:example.search",
                "component_type": "capability",
                "name": "Example search",
                "reference_id": "example.search",
                "reference_version": "2.0.0",
                "metadata": {},
            }
        ],
    )

    assert store.list_extension_components("example.discover", "1.0.0")[0][
        "reference_version"
    ] == "1.0.0"
    assert store.list_extension_components("example.discover", "2.0.0")[0][
        "reference_version"
    ] == "2.0.0"


def test_allowed_extension_catalog_is_discovered_without_agent_worker(tmp_path: Path) -> None:
    store = _store(tmp_path)
    values = discover_allowed_extensions(
        Config(
            extensions=ExtensionsConfig(
                allowed_ids=["capability-media-generation"],
                discover_entry_points=True,
            )
        ),
        store=store,
    )

    assert values == [
        {
            "extension_id": "capability-media-generation",
            "version": "1.0.0",
            "type": "capability",
        }
    ]
    assert store.get_extension_release("capability-media-generation")["status"] == "discovered"
    assert store.get_capability_release_definition("image.generate", "1.0.0") is not None


def test_extension_release_activates_only_after_exact_worker_load_ack(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register_runtime_worker(
        worker_id="agent-extension-a",
        capabilities={"agent": True},
        metadata={
            "extensions": [
                {
                    "extension_id": "example.discover",
                    "version": "1.0.0",
                    "build_digest": TEST_BUILD_DIGEST,
                }
            ]
        },
    )

    rollout_id = store.stage_extension_release(
        "example.discover", "1.0.0", actor_id="release-admin"
    )
    assert store.get_extension_release("example.discover", "1.0.0")["status"] == "staged"
    assert store.get_active_extension_release("example.discover") is None
    assert store.acknowledge_configuration_revision(
        worker_id="agent-extension-a",
        aggregate_type="extension",
        aggregate_id="example.discover",
        revision_id="1.0.0",
    )

    assert store.get_configuration_rollout(rollout_id).status == "completed"
    assert store.get_active_extension_release("example.discover")["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_channel_extension_rollout_targets_and_is_acked_by_channel_worker(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    manifest = _extension_manifest("channel-example", "channel")
    release = manifest.to_release_dict()
    store.upsert_extension_release(release)
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

    rollout_id = store.stage_extension_release(
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
        {"worker_id": "channel-worker-a", "extension_releases": [release]},
    )()
    assert await ExtensionRolloutWatcher(store=store, runtime=runtime).refresh_pending() == 1
    assert store.get_active_extension_release(manifest.extension_id)["version"] == "1.0.0"


def test_provider_extension_rollout_targets_agent_workers(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = _extension_manifest("provider-example", "model_provider")
    store.upsert_extension_release(manifest.to_release_dict())
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

    rollout_id = store.stage_extension_release(
        manifest.extension_id,
        manifest.version,
        actor_id="release-admin",
    )
    assert [
        item["worker_id"]
        for item in store.list_configuration_rollout_targets(rollout_id)
    ] == ["agent-worker-a"]


def test_extension_release_digest_cannot_be_overwritten(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="immutable"):
        store.upsert_extension_release(
            CapabilityExtensionManifest(
                extension_id="example.discover",
                version="1.0.0",
                name="Example Discover",
                build_digest=OTHER_BUILD_DIGEST,
            ).to_release_dict()
        )


def test_extension_manifest_and_component_catalog_are_immutable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="manifest is immutable"):
        store.upsert_extension_release(
            CapabilityExtensionManifest(
                extension_id="example.discover",
                version="1.0.0",
                name="Renamed release",
                build_digest=TEST_BUILD_DIGEST,
            ).to_release_dict()
        )
    with pytest.raises(ValueError, match="component is immutable"):
        store.sync_extension_components(
            "example.discover",
            "1.0.0",
            [
                {
                    "component_id": "capability:example.search",
                    "component_type": "capability",
                    "name": "Mutated search",
                    "reference_id": "example.search",
                    "reference_version": "1.0.0",
                    "metadata": {},
                }
            ],
        )


def test_extension_control_plane_api_requires_admin_and_projects_safe_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_api_access_token(
        user_id="operator", actor_id="test", token="owner-token"
    )
    container = build_api_container(config=Config(), store=store)
    client = TestClient(create_app(container))
    with client:
        denied = client.get(
            "/control/v1/admin/extensions",
            headers={"Authorization": "Bearer owner-token"},
        )
        assert denied.status_code == 403
        store.upsert_platform_admin(user_id="operator", permissions=["*"], actor_id="test")
        store.create_operator_access_token(
            user_id="operator", actor_id="test", token="extension-token"
        )
        still_denied = client.get(
            "/control/v1/admin/extensions",
            headers={"Authorization": "Bearer owner-token"},
        )
        assert still_denied.status_code == 403
        listed = client.get("/control/v1/admin/extensions", headers={"Authorization": "Bearer extension-token"})
        assert listed.status_code == 200
        assert listed.json()["items"][0]["extension_id"] == "example.discover"
        assert "plugin_id" not in listed.json()["items"][0]
        detail = client.get(
            "/control/v1/admin/extensions/example.discover", headers={"Authorization": "Bearer extension-token"}
        )
        assert detail.status_code == 200
        metadata = detail.json()["components"][0]["metadata"]
        assert metadata["runtime_enabled"] is True
        assert metadata["worker_loaded"] is False
        assert metadata["execution_ready"] is False
        assert metadata["execution_blockers"] == [
            "Capability 版本尚未发布",
            "没有 Worker 加载当前扩展版本",
        ]


def test_console_activation_toggles_durable_desired_state_within_allowlist(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.register_runtime_worker(
        worker_id="agent-extension-console",
        capabilities={"agent": True},
        metadata={
            "extensions": [
                {
                    "extension_id": "example.discover",
                    "version": "1.0.0",
                    "build_digest": TEST_BUILD_DIGEST,
                }
            ]
        },
    )
    store.stage_extension_release("example.discover", "1.0.0", actor_id="test")
    store.acknowledge_configuration_revision(
        worker_id="agent-extension-console",
        aggregate_type="extension",
        aggregate_id="example.discover",
        revision_id="1.0.0",
    )
    store.create_operator_access_token(
        user_id="operator", actor_id="test", token="extension-activation-token"
    )
    store.upsert_platform_admin(user_id="operator", permissions=["*"], actor_id="test")
    config = Config(
        extensions=ExtensionsConfig(
            allowed_ids=["example.discover"], allow_console_activation=True
        )
    )
    container = build_api_container(config=config, store=store)
    store.sync_extension_inventory(
        [
            {
                "extension_id": "example.discover",
                "name": "Example Discover",
                "extension_types": ["capability"],
                "source_available": True,
                "installed": True,
                "metadata": {},
            }
        ],
        allowed_ids={"example.discover"},
        initially_active_ids=set(),
    )
    store.set_extension_desired_active("example.discover", False, actor_id="test")
    headers = {"Authorization": "Bearer extension-activation-token"}

    with TestClient(create_app(container)) as client:
        activated = client.post(
            "/control/v1/admin/extensions/example.discover/activate", headers=headers
        )
        deactivated = client.post(
            "/control/v1/admin/extensions/example.discover/deactivate", headers=headers
        )

    assert activated.status_code == 202
    assert activated.json()["desired_active"] is True
    assert activated.json()["effective_active"] is True
    assert deactivated.status_code == 200
    assert deactivated.json()["desired_active"] is False
    assert deactivated.json()["effective_active"] is False


def test_extension_health_aggregates_worker_advertised_checks(tmp_path: Path) -> None:
    store = _store(tmp_path)
    release = {
        "extension_id": "example.discover",
        "version": "1.0.0",
        "build_digest": TEST_BUILD_DIGEST,
        "health_checks": [
            {
                "name": "provider_credentials",
                "status": "degraded",
                "summary": "set the provider credential on this Worker",
            }
        ],
    }
    store.register_runtime_worker(
        worker_id="agent-extension-health",
        capabilities={"agent": True},
        metadata={"extensions": [release]},
    )
    store.stage_extension_release("example.discover", "1.0.0", actor_id="test")
    store.acknowledge_configuration_revision(
        worker_id="agent-extension-health",
        aggregate_type="extension",
        aggregate_id="example.discover",
        revision_id="1.0.0",
    )
    store.create_operator_access_token(
        user_id="operator", actor_id="test", token="extension-health-token"
    )
    store.upsert_platform_admin(user_id="operator", permissions=["*"], actor_id="test")
    container = build_api_container(config=Config(), store=store)
    with TestClient(create_app(container)) as client:
        response = client.get(
            "/control/v1/admin/extensions/example.discover/health",
            headers={"Authorization": "Bearer extension-health-token"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"][-1] == {
        "name": "provider_credentials",
        "status": "degraded",
        "summary": "set the provider credential on this Worker",
    }


def test_capability_runtime_settings_support_prepublication_configuration(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.discover_capability_release(
        CapabilityDefinition(
            ref=CapabilityRef("example.search", "1.0.0", CapabilityKind.CAPABILITY, "test.extension", "1.0.0", "sha256:test"),
            name="Example search", description="", input_schema={"type": "object"}, output_schema={"type": "object"}, adapter="example.search",
            configuration_schema={"type": "object", "additionalProperties": False, "properties": {"limit": {"type": "integer"}}},
        )
    )
    store.create_operator_access_token(
        user_id="operator",
        actor_id="test",
        token="runtime-settings-token",
        role="viewer",
        permissions=[],
    )
    container = build_api_container(config=Config(), store=store)
    client = TestClient(create_app(container))
    headers = {"Authorization": "Bearer runtime-settings-token"}
    path = "/control/v1/admin/capabilities/example.search/runtime-settings?version=1.0.0"
    with client:
        assert client.put(path, headers=headers, json={"enabled": False, "configuration": {"limit": 5}}).status_code == 403
        store.upsert_platform_admin(user_id="root-admin", permissions=["*"], actor_id="bootstrap")
        store.upsert_platform_admin(user_id="operator", permissions=["capabilities.read", "capabilities.publish"], actor_id="test")
        assert client.get(path, headers=headers).status_code == 200
        response = client.put(path, headers=headers, json={"enabled": False, "configuration": {"limit": 5}})
        assert response.status_code == 200
        assert response.json()["enabled"] is False
        invalid = client.put(path, headers=headers, json={"enabled": True, "configuration": {"limit": "five"}})
        assert invalid.status_code == 422


def test_extension_playground_creates_a_direct_durable_capability_run(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.publish_capability(
        CapabilityDefinition(
            ref=CapabilityRef(
                "example.search", "1.0.0", CapabilityKind.CAPABILITY,
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
    store.create_operator_access_token(
        user_id="operator", actor_id="test", token="playground-token"
    )
    store.upsert_platform_admin(user_id="operator", permissions=["*"], actor_id="test")
    container = build_api_container(config=Config(), store=store)
    client = TestClient(create_app(container))
    with client:
        response = client.post(
            "/control/v1/admin/extensions/example.discover/playground/runs",
            headers={"Authorization": "Bearer playground-token"},
            json={"capability_id": "example.search", "input": {"query": "Ada"}},
        )
    assert response.status_code == 202
    body = response.json()
    tasks = store.list_runtime_tasks(run_id=body["run_id"], limit=10)
    assert body["prompt"] == "Capability Playground: example.search"
    assert len(tasks) == 1
    assert tasks[0].payload["capability"]["capability_id"] == "example.search"
    assert tasks[0].payload["capability_input"] == {"query": "Ada"}
    assert store.get_runtime_run(body["run_id"]).options["aggregate"] is False
