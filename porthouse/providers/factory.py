"""Create native model adapters from infrastructure configuration."""

from __future__ import annotations

from typing import Any

import httpx

from porthouse.contracts.extensions import ModelProviderBuildRequest
from porthouse.providers.base import LLMProvider
from porthouse.providers.registry import find_by_name, get_provider_registry
from porthouse.providers.unconfigured import UnconfiguredModelProvider


def _validate_ascii_api_key(provider_name: str, api_key: Any) -> str:
    value = str(api_key or "")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RuntimeError(
            f"provider {provider_name!r} API key must contain ASCII characters only"
        ) from exc
    return value


def create_model_provider(
    *,
    config: Any,
    model: str,
    api_key: str | None = None,
    api_base: str | None = None,
    extra_headers: dict[str, str] | None = None,
    provider_name: str | None = None,
    client: httpx.AsyncClient | None = None,
    model_policy: dict[str, Any] | None = None,
    request_timeout_seconds: float = 120.0,
) -> LLMProvider:
    if str(model).strip() == "unconfigured/model" and not str(provider_name or "").strip():
        return UnconfiguredModelProvider()
    name = str(provider_name or config.get_provider_name(model) or "").strip()
    provider_config = config.get_provider(model)
    key = api_key if api_key is not None else (provider_config.api_key if provider_config else "")
    base = api_base or config.get_api_base(model)
    headers = dict(extra_headers or (provider_config.extra_headers if provider_config else {}) or {})
    spec = find_by_name(config, name)
    if spec is None:
        raise RuntimeError(f"unsupported model provider for {model!r}")
    if not base:
        raise RuntimeError(f"provider {name!r} requires api_base")
    if not key and not spec.is_local:
        raise RuntimeError(f"provider {name!r} requires an API key")
    normalized_key = _validate_ascii_api_key(name, key)
    extension = get_provider_registry(config).extension_for(name)
    if extension is None:
        raise RuntimeError(
            f"model provider extension for {name!r} is not installed or failed to load"
        )
    catalog_item = next(
        (
            dict(item)
            for item in (getattr(provider_config, "models", None) or [])
            if str(item.get("model_id") or "") == str(model)
        ),
        {},
    )
    pricing_keys = (
        "input_cost_per_million_tokens",
        "output_cost_per_million_tokens",
        "cached_input_cost_per_million_tokens",
        "cache_creation_input_cost_per_million_tokens",
    )
    usage_pricing = {
        key: catalog_item.get(key)
        for key in pricing_keys
        if catalog_item.get(key) is not None
    }
    if catalog_item:
        usage_pricing["model_id"] = str(catalog_item.get("model_id") or model)
    revision_id = getattr(provider_config, "revision_id", None)
    if revision_id:
        usage_pricing["provider_revision_id"] = str(revision_id)
    return extension.factory(
        ModelProviderBuildRequest(
            provider_name=name,
            api_key=normalized_key,
            api_base=base,
            default_model=model,
            extra_headers=headers,
            reasoning_options=dict(model_policy or {}),
            usage_pricing=usage_pricing,
            request_timeout_seconds=float(request_timeout_seconds),
            client=client,
        )
    )
