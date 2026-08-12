from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from joyhousebot_provider_openai_compatible import (
    OPENAI_COMPATIBLE_PROVIDER_EXTENSION,
)

from joyhousebot.api.app import create_app
from joyhousebot.application.model_providers import ModelProviderService
from joyhousebot.bootstrap.agent_runtime_catalog import AgentRuntimeCatalog
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config, ExtensionsConfig
from joyhousebot.domain.model_providers import (
    materialize_model_provider,
    normalize_model_provider,
    validate_agent_model_policy,
)
from tests.support.postgres_store import PostgresTestStore


def _model(model_id: str = "openrouter/openai/gpt-test") -> dict:
    return {
        "model_id": model_id,
        "name": "Test model",
        "description": "Provider control-plane test model",
        "kind": "llm",
        "enabled": True,
        "input_modalities": ["text", "image"],
        "context_window": 128000,
        "max_output_tokens": 8192,
        "supports_tools": True,
        "supports_reasoning": True,
        "supports_structured_output": True,
        "default_temperature": 0.2,
        "tags": ["test"],
    }


def _configuration(*, model_id: str = "openrouter/openai/gpt-test") -> dict:
    return {
        "enabled": True,
        "extension_id": "provider-openai-compatible",
        "api_base": "https://models.example.test/v1",
        "api_key_ref": "env://MODEL_PROVIDER_TEST_KEY",
        "credential_mode": "api_key",
        "extra_header_refs": {"X-Test-Account": "env://MODEL_PROVIDER_TEST_ACCOUNT"},
        "request_timeout_seconds": 45,
        "models": [_model(model_id)],
    }


def _store(tmp_path: Path) -> PostgresTestStore:
    return PostgresTestStore(tmp_path / "model-provider-control.db")


def _activate_extension(store: PostgresTestStore, worker_id: str = "agent-model-a") -> dict:
    release = OPENAI_COMPATIBLE_PROVIDER_EXTENSION.manifest.to_release_dict()
    store.upsert_plugin_release(release)
    store.register_runtime_worker(
        worker_id=worker_id,
        capabilities={"agent": True},
        metadata={"extensions": [release]},
    )
    store.stage_plugin_release(
        release["plugin_id"], release["version"], actor_id="test"
    )
    assert store.acknowledge_configuration_revision(
        worker_id=worker_id,
        aggregate_type="plugin",
        aggregate_id=release["plugin_id"],
        revision_id=release["version"],
    )
    return release


def test_model_provider_requires_secret_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="env://VARIABLE"):
        normalize_model_provider(
            "openrouter", {**_configuration(), "api_key_ref": "plain-secret"}
        )
    normalized = normalize_model_provider("openrouter", _configuration())
    monkeypatch.setenv("MODEL_PROVIDER_TEST_KEY", "provider-secret")
    monkeypatch.setenv("MODEL_PROVIDER_TEST_ACCOUNT", "account-1")
    materialized = materialize_model_provider(normalized)
    assert materialized["api_key"] == "provider-secret"
    assert materialized["extra_headers"] == {"X-Test-Account": "account-1"}
    assert "api_key_ref" not in materialized

    with pytest.raises(ValueError, match="output limit"):
        validate_agent_model_policy(
            {"primary": "openrouter/openai/gpt-test", "max_tokens": 9000},
            normalized["models"],
        )


def test_model_provider_rollout_and_safe_rollback(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _activate_extension(store)
    first = store.save_model_provider_revision(
        "openrouter",
        name="OpenRouter",
        description="Gateway",
        configuration=_configuration(),
        actor_id="admin",
    )
    rollout = store.stage_model_provider_revision(
        "openrouter", first["revision_id"], actor_id="admin"
    )
    assert store.acknowledge_configuration_revision(
        worker_id="agent-model-a",
        aggregate_type="model_provider",
        aggregate_id="openrouter",
        revision_id="openrouter:v1",
    )
    assert store.get_configuration_rollout(rollout).status == "completed"
    assert store.list_active_models()[0]["model_id"] == "openrouter/openai/gpt-test"

    second = store.save_model_provider_revision(
        "openrouter",
        name="OpenRouter",
        description="Gateway",
        configuration=_configuration(model_id="openrouter/anthropic/claude-test"),
        actor_id="admin",
    )
    second_rollout = store.stage_model_provider_revision(
        "openrouter", second["revision_id"], actor_id="admin"
    )
    assert store.acknowledge_configuration_revision(
        worker_id="agent-model-a",
        aggregate_type="model_provider",
        aggregate_id="openrouter",
        revision_id="openrouter:v2",
    )
    assert store.list_active_models()[0]["model_id"].endswith("claude-test")
    assert store.rollback_configuration_rollout(second_rollout, actor_id="operator")
    assert store.acknowledge_configuration_revision(
        worker_id="agent-model-a",
        aggregate_type="model_provider",
        aggregate_id="openrouter",
        revision_id="openrouter:v1",
    )
    assert store.list_active_models()[0]["model_id"].endswith("gpt-test")


@pytest.mark.asyncio
async def test_provider_publish_cannot_break_active_agent_models(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _activate_extension(store)
    service = ModelProviderService(store)
    first = store.save_model_provider_revision(
        "test",
        name="Test Provider",
        description="Initial catalog",
        configuration=_configuration(model_id="test/default"),
        actor_id="admin",
    )
    published = await service.publish_revision(
        "test",
        first["revision_id"],
        actor_id="admin",
        rollout_policy={},
    )
    assert store.acknowledge_configuration_revision(
        worker_id="agent-model-a",
        aggregate_type="model_provider",
        aggregate_id="test",
        revision_id=first["revision_id"],
    )
    assert published["status"] == "staged"

    incompatible = store.save_model_provider_revision(
        "test",
        name="Test Provider",
        description="Breaking catalog",
        configuration=_configuration(model_id="test/replacement"),
        actor_id="admin",
    )
    with pytest.raises(ValueError, match="would break active Agent default"):
        await service.publish_revision(
            "test",
            incompatible["revision_id"],
            actor_id="admin",
            rollout_policy={},
        )


@pytest.mark.asyncio
async def test_first_provider_can_bootstrap_inert_unconfigured_agent(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(
        tmp_path / "first-provider-bootstrap.db",
        bootstrap_model="unconfigured/model",
    )
    _activate_extension(store)
    service = ModelProviderService(store)
    first = store.save_model_provider_revision(
        "test",
        name="Test Provider",
        description="First usable model catalog",
        configuration=_configuration(model_id="test/default"),
        actor_id="admin",
    )

    published = await service.publish_revision(
        "test",
        first["revision_id"],
        actor_id="admin",
        rollout_policy={},
    )

    assert published["status"] == "staged"
    assert store.get_agent_profile("default").revision.model_policy["primary"] == (
        "unconfigured/model"
    )


@pytest.mark.asyncio
async def test_worker_preheats_provider_and_applies_runtime_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    release = _activate_extension(store)
    revision = store.save_model_provider_revision(
        "openrouter",
        name="OpenRouter",
        description="Gateway",
        configuration=_configuration(),
        actor_id="admin",
    )
    config = Config(
        extensions=ExtensionsConfig(
            enabled=["provider-openai-compatible"], discover_entry_points=True
        )
    )
    catalog = AgentRuntimeCatalog(config=config, store=store)
    catalog._runtime = SimpleNamespace(  # noqa: SLF001
        worker_id="agent-model-a", plugin_releases=[release]
    )
    monkeypatch.setenv("MODEL_PROVIDER_TEST_KEY", "provider-secret")
    monkeypatch.setenv("MODEL_PROVIDER_TEST_ACCOUNT", "account-1")
    await catalog._preheat_model_provider(  # noqa: SLF001
        {
            "aggregate_type": "model_provider",
            "aggregate_id": "openrouter",
            "revision_id": revision["revision_id"],
        }
    )
    runtime_config = catalog._runtime_model_config(  # noqa: SLF001
        {"openrouter": _configuration()}
    )
    provider = runtime_config.providers.settings["openrouter"]
    assert provider.api_key == "provider-secret"
    assert provider.api_base == "https://models.example.test/v1"
    assert provider.request_timeout_seconds == 45


def test_model_provider_api_never_returns_secret_values(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_api_access_token(
        user_id="operator", actor_id="test", token="model-provider-token"
    )
    store.upsert_platform_admin(user_id="operator", permissions=["*"], actor_id="test")
    body = {
        "provider_id": "openrouter",
        "name": "OpenRouter",
        "description": "Gateway",
        **_configuration(),
    }
    container = build_api_container(config=Config(), store=store)
    headers = {"Authorization": "Bearer model-provider-token"}
    with TestClient(create_app(container)) as client:
        created = client.post("/v1/admin/model-providers", headers=headers, json=body)
        assert created.status_code == 201
        listed = client.get("/v1/admin/model-providers", headers=headers)
        assert listed.status_code == 200
        serialized = str(listed.json())
        assert "env://MODEL_PROVIDER_TEST_KEY" in serialized
        assert "provider-secret" not in serialized
        rejected = client.post(
            "/v1/admin/model-providers",
            headers=headers,
            json={**body, "provider_id": "bad", "api_key_ref": "plaintext"},
        )
        assert rejected.status_code == 422
