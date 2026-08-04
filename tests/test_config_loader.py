import os

import pytest

from joyhousebot.config.loader import (
    CONFIG_PATH_ENV,
    _apply_config_env_vars,
    convert_keys,
    get_config_path,
    load_config,
)
from joyhousebot.config.schema import Config, EnvConfig


def test_load_config_accepts_native_camel_case(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"gateway":{"channelSendWorkers":7},'
        '"env":{"vars":{"JOYHOUSEBOT_FEATURE_FLAG":"enabled"}}}'
    )
    loaded = load_config(path)
    assert loaded.gateway.channel_send_workers == 7
    assert loaded.env and loaded.env.vars == {"JOYHOUSEBOT_FEATURE_FLAG": "enabled"}


def test_convert_keys_preserves_environment_names() -> None:
    converted = convert_keys({"env": {"vars": {"API_KEY": "secret"}}})
    assert converted["env"]["vars"] == {"API_KEY": "secret"}


def test_load_config_rejects_plaintext_secrets_and_resolves_env_refs(
    tmp_path, monkeypatch
) -> None:
    plaintext = tmp_path / "plaintext.json"
    plaintext.write_text('{"providers":{"anthropic":{"apiKey":"secret"}}}')
    with pytest.raises(ValueError, match="plaintext secret"):
        load_config(plaintext)

    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "from-environment")
    referenced = tmp_path / "referenced.json"
    referenced.write_text(
        '{"providers":{"anthropic":{"apiKey":"env://TEST_ANTHROPIC_KEY"}}}'
    )
    assert load_config(referenced).providers.anthropic.api_key == "from-environment"


def test_apply_config_env_does_not_overwrite(monkeypatch) -> None:
    monkeypatch.setenv("JOYHOUSEBOT_TEST_KEY", "existing")
    config = Config(env=EnvConfig(vars={"JOYHOUSEBOT_TEST_KEY": "new"}))
    _apply_config_env_vars(config)
    assert os.environ["JOYHOUSEBOT_TEST_KEY"] == "existing"


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


def test_apply_config_env_vars_restricts_to_joyhousebot_prefix(monkeypatch) -> None:
    """env.vars may only set JOYHOUSEBOT_-prefixed keys (M5)."""
    monkeypatch.delenv("SOME_FOREIGN_KEY", raising=False)
    monkeypatch.delenv("JOYHOUSEBOT_ALLOWED_KEY", raising=False)
    config = Config(
        env=EnvConfig(vars={"SOME_FOREIGN_KEY": "nope", "JOYHOUSEBOT_ALLOWED_KEY": "yes"})
    )
    _apply_config_env_vars(config)
    assert "SOME_FOREIGN_KEY" not in os.environ
    assert os.environ["JOYHOUSEBOT_ALLOWED_KEY"] == "yes"


def test_generic_llm_key_defaults_to_anthropic(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    path.write_text("{}")
    monkeypatch.setenv("LLM_API_KEY", "generic-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    loaded = load_config(path)

    assert loaded.providers.anthropic.api_key == "generic-key"
    assert loaded.get_provider_name("anthropic/claude-opus-4.5") == "anthropic"


def test_generic_llm_provider_and_base_are_explicit(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    path.write_text("{}")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_API_KEY", "gateway-key")
    monkeypatch.setenv("LLM_API_BASE", "https://models.example/v1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    loaded = load_config(path)

    assert loaded.providers.openrouter.api_key == "gateway-key"
    assert loaded.providers.openrouter.api_base == "https://models.example/v1"
    assert loaded.providers.default_provider == "openrouter"
    assert loaded.get_provider_name("anthropic/claude-opus-4.5") == "openrouter"


def test_explicit_generic_provider_wins_over_unrelated_native_key(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "config.json"
    path.write_text("{}")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_API_KEY", "gateway-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "你的密钥")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    loaded = load_config(path)

    assert loaded.get_provider_name("anthropic/claude-opus-4.5") == "openrouter"
    assert loaded.get_provider("anthropic/claude-opus-4.5") is loaded.providers.openrouter


def test_provider_specific_key_wins_over_generic_key(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    path.write_text("{}")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "generic-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "native-key")

    loaded = load_config(path)

    assert loaded.providers.anthropic.api_key == "native-key"


def test_invalid_generic_llm_provider_fails(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    path.write_text("{}")
    monkeypatch.setenv("LLM_PROVIDER", "not-a-provider")
    monkeypatch.setenv("LLM_API_KEY", "generic-key")

    with pytest.raises(ValueError, match="unsupported LLM_PROVIDER"):
        load_config(path)
