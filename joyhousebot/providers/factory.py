"""Create native model adapters from infrastructure configuration."""

from __future__ import annotations

from typing import Any

import httpx

from joyhousebot.providers.anthropic import AnthropicProvider
from joyhousebot.providers.base import LLMProvider
from joyhousebot.providers.openai_compatible import OpenAICompatibleProvider
from joyhousebot.providers.registry import find_by_name


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
) -> LLMProvider:
    name = str(provider_name or config.get_provider_name(model) or "").strip()
    provider_config = config.get_provider(model)
    key = api_key if api_key is not None else (provider_config.api_key if provider_config else "")
    base = api_base or config.get_api_base(model)
    headers = dict(extra_headers or (provider_config.extra_headers if provider_config else {}) or {})
    spec = find_by_name(name)
    if spec is None:
        raise RuntimeError(f"unsupported model provider for {model!r}")
    if not base:
        raise RuntimeError(f"provider {name!r} requires api_base")
    if not key and not spec.is_local:
        raise RuntimeError(f"provider {name!r} requires an API key")
    normalized_key = _validate_ascii_api_key(name, key)
    if spec.protocol == "anthropic":
        return AnthropicProvider(
            api_key=normalized_key,
            api_base=base,
            default_model=model,
            extra_headers=headers,
            reasoning_options=model_policy,
            client=client,
        )
    return OpenAICompatibleProvider(
        api_key=normalized_key,
        api_base=base,
        default_model=model,
        provider_name=name,
        extra_headers=headers,
        reasoning_options=model_policy,
        client=client,
    )
