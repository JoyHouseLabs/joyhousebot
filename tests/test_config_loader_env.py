"""Tests for env config migration and _apply_config_env_vars (OpenClaw env.vars compat)."""

import os
from pathlib import Path

from joyhousebot.config.loader import (
    _apply_config_env_vars,
    _migrate_config,
    convert_keys,
    load_config,
    load_config_from_openclaw_file,
)
from joyhousebot.config.schema import Config, EnvConfig


# --- _migrate_config: OpenClaw env → joyhousebot env.vars ---


def test_migrate_env_vars_only() -> None:
    """env.vars only → data['env'] = {'vars': {...}} (camelCase for convert_keys)."""
    data = {"env": {"vars": {"API_KEY": "secret", "BASE_URL": "https://api.example.com"}}}
    migrated = _migrate_config(data)
    assert migrated["env"] == {"vars": {"API_KEY": "secret", "BASE_URL": "https://api.example.com"}}


def test_migrate_env_sugar_string_keys() -> None:
    """Top-level env string keys (sugar) merged into vars; shellEnv and vars key excluded."""
    data = {
        "env": {
            "EXTRA_FOO": "bar",
            "EXTRA_BAZ": "qux",
            "shellEnv": {"enabled": True},
        }
    }
    migrated = _migrate_config(data)
    assert migrated["env"]["vars"]["EXTRA_FOO"] == "bar"
    assert migrated["env"]["vars"]["EXTRA_BAZ"] == "qux"
    assert "shellEnv" not in migrated["env"]["vars"]


def test_migrate_env_vars_and_sugar_merged() -> None:
    """env.vars and sugar string keys merged; sugar overwrites same key (later iteration)."""
    data = {
        "env": {
            "vars": {"A": "from_vars", "B": "from_vars"},
            "A": "from_sugar",
            "C": "from_sugar",
        }
    }
    migrated = _migrate_config(data)
    vars_merged = migrated["env"]["vars"]
    assert vars_merged["A"] == "from_sugar"
    assert vars_merged["B"] == "from_vars"
    assert vars_merged["C"] == "from_sugar"


def test_migrate_env_non_string_sugar_ignored() -> None:
    """Only string values under env (sugar) are merged; numbers/objects ignored."""
    data = {"env": {"STR": "ok", "NUM": 42, "NESTED": {"x": 1}}}
    migrated = _migrate_config(data)
    assert migrated["env"]["vars"]["STR"] == "ok"
    assert "NUM" not in migrated["env"]["vars"]
    assert "NESTED" not in migrated["env"]["vars"]


def test_migrate_env_no_env_block() -> None:
    """No env key → data unchanged for env (no KeyError)."""
    data = {"agents": {"defaults": {"model": "gpt-4"}}}
    migrated = _migrate_config(data)
    assert "env" not in migrated


# --- _apply_config_env_vars: setdefault behavior ---


def test_apply_config_env_vars_sets_missing_key() -> None:
    """_apply_config_env_vars sets os.environ for keys not already set."""
    key = "JOYHOUSEBOT_TEST_ENV_MISSING_KEY"
    try:
        if key in os.environ:
            del os.environ[key]
        cfg = Config(env=EnvConfig(vars={key: "test_value"}))
        _apply_config_env_vars(cfg)
        assert os.environ.get(key) == "test_value"
    finally:
        if key in os.environ:
            del os.environ[key]


def test_apply_config_env_vars_does_not_overwrite_existing() -> None:
    """_apply_config_env_vars uses setdefault: existing value is preserved."""
    key = "JOYHOUSEBOT_TEST_ENV_EXISTING_KEY"
    original = "already_set"
    try:
        os.environ[key] = original
        cfg = Config(env=EnvConfig(vars={key: "would_overwrite"}))
        _apply_config_env_vars(cfg)
        assert os.environ.get(key) == original
    finally:
        if key in os.environ:
            del os.environ[key]


def test_apply_config_env_vars_no_op_when_env_none() -> None:
    """_apply_config_env_vars does nothing when cfg.env is None."""
    cfg = Config()
    cfg.env = None
    _apply_config_env_vars(cfg)  # no raise, no side effect


def test_apply_config_env_vars_no_op_when_vars_empty() -> None:
    """_apply_config_env_vars does nothing when cfg.env.vars is None or empty."""
    cfg = Config(env=EnvConfig(vars=None))
    _apply_config_env_vars(cfg)
    cfg2 = Config(env=EnvConfig(vars={}))
    _apply_config_env_vars(cfg2)


# --- load_config_from_openclaw_file with env ---


def test_load_from_openclaw_file_env_present(tmp_path: Path) -> None:
    """Loading OpenClaw JSON with env produces config.env and config.env.vars."""
    openclaw_json = tmp_path / "openclaw.json"
    openclaw_json.write_text(
        '{"env":{"vars":{"X":"y"}},\n'
        '"channels":{"telegram":{"enabled":false}},\n'
        '"gateway":{"port":18888}}\n',
        encoding="utf-8",
    )
    cfg = load_config_from_openclaw_file(openclaw_json)
    assert cfg.env is not None
    assert cfg.env.vars is not None
    # env.vars keys are preserved (env var names, not config keys)
    assert cfg.env.vars.get("X") == "y"


def test_load_from_openclaw_file_env_sugar_merged(tmp_path: Path) -> None:
    """OpenClaw env sugar (string keys) are merged into config.env.vars after convert_keys."""
    openclaw_json = tmp_path / "openclaw.json"
    openclaw_json.write_text(
        '{"env":{"SUGAR_KEY":"sugar_val","vars":{"VARS_KEY":"vars_val"}},\n'
        '"channels":{"telegram":{"enabled":false}},\n'
        '"gateway":{"port":18889}}\n',
        encoding="utf-8",
    )
    cfg = load_config_from_openclaw_file(openclaw_json)
    assert cfg.env is not None and cfg.env.vars is not None
    # env.vars keys preserved (env var names)
    assert cfg.env.vars.get("VARS_KEY") == "vars_val"
    assert cfg.env.vars.get("SUGAR_KEY") == "sugar_val"


def test_convert_keys_env_vars_keys_preserved() -> None:
    """env.vars keys (env var names) are not converted; only config keys like env/vars are."""
    data = _migrate_config({"env": {"vars": {"API_KEY": "secret", "FOO_BAR": "baz"}}})
    converted = convert_keys(data)
    assert converted.get("env") is not None
    assert converted["env"].get("vars") == {"API_KEY": "secret", "FOO_BAR": "baz"}


# --- load_config first-run init (default path only) ---


def test_load_config_creates_default_config_on_first_run(tmp_path: Path, monkeypatch: object) -> None:
    """When default config path does not exist, load_config creates ~/.joyhousebot and writes default config."""
    default_cfg = tmp_path / "config.json"
    monkeypatch.setattr("joyhousebot.config.loader.get_config_path", lambda: default_cfg)
    assert not default_cfg.exists()
    cfg = load_config()
    assert default_cfg.exists()
    assert cfg is not None
    assert default_cfg.parent == tmp_path
    # Reload from file and ensure it matches schema (has expected top-level keys).
    cfg2 = load_config(default_cfg)
    assert hasattr(cfg2, "agents")
    assert hasattr(cfg2, "gateway")


def test_load_config_non_default_path_returns_default_object_without_writing(tmp_path: Path) -> None:
    """When a non-default path does not exist, load_config returns Config() and does not create the file."""
    other = tmp_path / "other.json"
    assert not other.exists()
    cfg = load_config(other)
    assert not other.exists()
    assert cfg is not None
    assert hasattr(cfg, "agents")
