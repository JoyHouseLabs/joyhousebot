import pytest

from joyhousebot.config.loader import (
    CONFIG_PATH_ENV,
    get_config_path,
    load_config,
)
from joyhousebot.config.schema import Config
from joyhousebot.providers.factory import create_model_provider


def test_load_config_accepts_native_camel_case(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"gateway":{"channelSendWorkers":7}}')
    loaded = load_config(path)
    assert loaded.gateway.channel_send_workers == 7


def test_load_config_rejects_plaintext_secrets_and_resolves_env_refs(
    tmp_path, monkeypatch
) -> None:
    plaintext = tmp_path / "plaintext.json"
    plaintext.write_text(
        '{"providers":{"settings":{"anthropic":{"apiKey":"secret"}}}}'
    )
    with pytest.raises(ValueError, match="plaintext secret"):
        load_config(plaintext)

    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "from-environment")
    referenced = tmp_path / "referenced.json"
    referenced.write_text(
        '{"providers":{"settings":{"anthropic":'
        '{"apiKey":"env://TEST_ANTHROPIC_KEY"}}}}'
    )
    assert (
        load_config(referenced).providers.settings["anthropic"].api_key
        == "from-environment"
    )


def test_extension_settings_resolve_secrets_without_core_provider_schema(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("EMAIL_TEST_PASSWORD", "mail-secret")
    path = tmp_path / "config.json"
    path.write_text(
        '{"extensions":{"allowedIds":["channel-email"],'
        '"discoverEntryPoints":true,'
        '"settings":{"channel-email":{"consentGranted":true,'
        '"imapPassword":"env://EMAIL_TEST_PASSWORD"}}}}'
    )

    loaded = load_config(path)

    assert loaded.extensions.allowed_ids == ["channel-email"]
    assert loaded.extensions.discover_entry_points is True
    assert loaded.extensions.settings["channel-email"] == {
        "consent_granted": True,
        "imap_password": "mail-secret",
    }


def test_extension_settings_reject_vendor_specific_plaintext_secret(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"extensions":{"settings":{"channel-email":{"smtpPassword":"plaintext"}}}}'
    )
    with pytest.raises(ValueError, match="plaintext secret"):
        load_config(path)


def test_model_provider_settings_do_not_require_new_core_schema_fields(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MODEL_EXTENSION_KEY", "extension-key")
    path = tmp_path / "provider-settings.json"
    path.write_text(
        '{"extensions":{"allowedIds":["provider-openai-compatible"]},'
        '"providers":{"defaultProvider":"openrouter","settings":'
        '{"openrouter":{"apiKey":"env://MODEL_EXTENSION_KEY",'
        '"apiBase":"https://models.example/v1"}}}}'
    )

    loaded = load_config(path)

    provider = loaded.providers.get_provider_config("openrouter")
    assert provider is loaded.providers.settings["openrouter"]
    assert provider.api_key == "extension-key"
    assert loaded.get_provider_name("anthropic/claude-test") == "openrouter"


def test_config_loading_does_not_discover_or_import_provider_extensions(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "provider-isolation.json"
    path.write_text(
        '{"extensions":{"allowedIds":["provider-not-installed"]}}'
    )
    monkeypatch.setenv("LLM_PROVIDER", "not-installed")
    monkeypatch.setenv("LLM_API_KEY", "deployment-key")

    def fail_discovery(_group):
        raise AssertionError("configuration loading must not discover extensions")

    monkeypatch.setattr("joyhousebot.extension_discovery.entry_points", fail_discovery)
    loaded = load_config(path)

    assert loaded.providers.default_provider == "not-installed"
    assert loaded.providers.settings["not-installed"].api_key == "deployment-key"


def test_agent_bootstrap_model_is_provider_neutral(monkeypatch) -> None:
    monkeypatch.delenv("LLM_MODEL", raising=False)
    config = Config()
    assert config.get_bootstrap_model() == "unconfigured/model"

    config.runtime.bootstrap_model = "provider/exact-model-v1"
    assert config.get_bootstrap_model() == "provider/exact-model-v1"


def test_llm_model_env_populates_bootstrap_model(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "provider/env-model-v2")
    path = tmp_path / "config.json"
    path.write_text("{}")
    assert load_config(path).runtime.bootstrap_model == "provider/env-model-v2"


def test_missing_explicit_path_fails(tmp_path) -> None:
    path = tmp_path / "missing.json"
    try:
        load_config(path)
    except ValueError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("an explicitly selected missing config must fail")


def test_config_path_environment_selects_deployment_file(monkeypatch, tmp_path) -> None:
    path = tmp_path / "cloud.json"
    path.write_text("{}")
    monkeypatch.setenv(CONFIG_PATH_ENV, str(path))
    assert get_config_path() == path
    assert isinstance(load_config(), Config)


def test_missing_environment_config_path_fails(monkeypatch, tmp_path) -> None:
    path = tmp_path / "missing.json"
    monkeypatch.setenv(CONFIG_PATH_ENV, str(path))
    try:
        load_config()
    except ValueError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("a missing deployment config must fail")


def test_generic_llm_key_requires_explicit_provider(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    path.write_text("{}")
    monkeypatch.setenv("LLM_API_KEY", "generic-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ValueError, match="LLM_PROVIDER is required"):
        load_config(path)


def test_generic_llm_provider_and_base_are_explicit(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"extensions":{"allowedIds":["provider-openai-compatible"]}}')
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_API_KEY", "gateway-key")
    monkeypatch.setenv("LLM_API_BASE", "https://models.example/v1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    loaded = load_config(path)

    assert loaded.providers.settings["openrouter"].api_key == "gateway-key"
    assert loaded.providers.settings["openrouter"].api_base == "https://models.example/v1"
    assert loaded.providers.default_provider == "openrouter"
    assert loaded.get_provider_name("anthropic/claude-opus-4.5") == "openrouter"


def test_explicit_generic_provider_wins_over_unrelated_native_key(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"extensions":{"allowedIds":["provider-openai-compatible"]}}')
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_API_KEY", "gateway-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "你的密钥")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    loaded = load_config(path)

    assert loaded.get_provider_name("anthropic/claude-opus-4.5") == "openrouter"
    assert (
        loaded.get_provider("anthropic/claude-opus-4.5")
        is loaded.providers.settings["openrouter"]
    )


def test_provider_specific_key_wins_over_generic_key(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"extensions":{"allowedIds":["provider-anthropic"]}}')
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "generic-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "native-key")

    loaded = load_config(path)

    assert loaded.get_provider_name("anthropic/claude-test") == "anthropic"
    assert loaded.providers.settings["anthropic"].api_key == "native-key"


def test_named_provider_configuration_is_rejected(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"providers":{"anthropic":{}}}')
    with pytest.raises(ValueError, match="anthropic"):
        load_config(path)


def test_invalid_generic_llm_provider_fails(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    path.write_text("{}")
    monkeypatch.setenv("LLM_PROVIDER", "not-a-provider")
    monkeypatch.setenv("LLM_API_KEY", "generic-key")

    loaded = load_config(path)
    with pytest.raises(RuntimeError, match="unsupported model provider"):
        create_model_provider(config=loaded, model="not-a-provider/model")
