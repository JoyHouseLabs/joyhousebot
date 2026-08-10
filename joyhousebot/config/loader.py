"""Native Joyhousebot configuration loading utilities.

Configuration is deployment input.  The cloud API never mutates it at runtime
and no foreign-client configuration formats are accepted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.config.schema import Config, ProviderConfig

CONFIG_PATH_ENV = "JOYHOUSEBOT_CONFIG_PATH"


def get_config_path() -> Path:
    configured = (os.environ.get(CONFIG_PATH_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".joyhousebot" / "config.json"


def load_config(config_path: Path | None = None) -> Config:
    """Load immutable deployment config, or defaults populated from environment."""
    explicitly_selected = config_path is not None or bool(
        (os.environ.get(CONFIG_PATH_ENV) or "").strip()
    )
    path = config_path or get_config_path()
    if not path.exists():
        if explicitly_selected:
            raise ValueError(f"Configured file does not exist: {path}")
        config = Config()
        _fill_provider_deployment_aliases(config)
        return config

    _warn_on_permissive_config_file(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("configuration root must be an object")
        converted = convert_keys(data)
        converted = _resolve_secret_references(converted)
        config = Config.model_validate(converted)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            f"Failed to load current Joyhousebot config from {path}: {exc}. "
            "Legacy client/standalone fields are intentionally unsupported; "
            "start from config.example.json and select it with --config or "
            f"{CONFIG_PATH_ENV}."
        ) from exc
    _fill_provider_deployment_aliases(config)
    return config


def _warn_on_permissive_config_file(path: Path) -> None:
    """Warn (without failing) when the config file is group/other readable."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & 0o077:
        logger.warning(
            "config file {} is readable by group/other (mode {:o}); "
            "it may contain secrets — consider `chmod 600 {}`",
            path,
            mode & 0o777,
            path,
        )


def _fill_provider_deployment_aliases(config: Config) -> None:
    """Capture provider-neutral deployment aliases without importing extensions."""
    bootstrap_model = (os.environ.get("LLM_MODEL") or "").strip()
    if bootstrap_model and not config.runtime.bootstrap_model.strip():
        config.runtime.bootstrap_model = bootstrap_model

    generic_key = (os.environ.get("LLM_API_KEY") or "").strip()
    generic_base = (os.environ.get("LLM_API_BASE") or "").strip()
    generic_name = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if not generic_key and not generic_base and "LLM_PROVIDER" not in os.environ:
        return
    if not generic_name:
        raise ValueError("LLM_PROVIDER is required when LLM_API_KEY or LLM_API_BASE is set")
    provider = config.providers.get_provider_config(generic_name)
    if provider is None:
        provider = ProviderConfig()
        config.providers.settings[generic_name] = provider
    config.providers.default_provider = generic_name
    if generic_key and not (provider.api_key or "").strip():
        provider.api_key = generic_key
    if generic_base and not (provider.api_base or "").strip():
        provider.api_base = generic_base


def convert_keys(data: Any) -> Any:
    """Convert native camelCase config keys while preserving env variable names."""
    if isinstance(data, list):
        return [convert_keys(item) for item in data]
    if not isinstance(data, dict):
        return data
    result: dict[str, Any] = {}
    for key, value in data.items():
        converted = camel_to_snake(key)
        result[converted] = convert_keys(value)
    return result


_SECRET_KEYS = {
    "api_key",
    "bridge_token",
    "client_secret",
    "control_token",
    "database_url",
    "password",
    "private_key",
    "refresh_token",
    "scenario_editor_tokens",
    "secret",
    "token",
    "user_tokens",
    "webhook_secret",
}


def _resolve_secret_references(value: Any, *, parent_key: str = "") -> Any:
    """Reject plaintext deployment secrets and resolve explicit env references."""
    if isinstance(value, list):
        return [_resolve_secret_references(item, parent_key=parent_key) for item in value]
    if not isinstance(value, dict):
        return value
    resolved: dict[str, Any] = {}
    for key, item in value.items():
        normalized = str(key).lower()
        sensitive = (
            normalized in _SECRET_KEYS
            or normalized.endswith(("_api_key", "_password", "_private_key", "_secret", "_token"))
            or parent_key == "extra_headers"
        )
        if sensitive and item not in (None, "", {}):
            if not isinstance(item, str) or not item.startswith("env://"):
                raise ValueError(
                    f"plaintext secret '{key}' is not allowed in config; use env://VARIABLE"
                )
            variable = item.removeprefix("env://").strip()
            if not variable:
                raise ValueError(f"secret reference '{key}' has no environment variable")
            secret = os.environ.get(variable)
            if secret is None:
                raise ValueError(
                    f"environment variable '{variable}' required by secret '{key}' is missing"
                )
            resolved[key] = secret
            continue
        resolved[key] = _resolve_secret_references(item, parent_key=normalized)
    return resolved


def camel_to_snake(name: str) -> str:
    chars: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)
