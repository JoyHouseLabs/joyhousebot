"""Versioned, secret-reference-only model provider configurations."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_EXTENSION_ID = re.compile(r"^provider-[a-z0-9][a-z0-9-]{0,119}$")
_ENV_REFERENCE = re.compile(r"^env://([A-Za-z_][A-Za-z0-9_]*)$")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,128}$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_MODEL_KINDS = frozenset(
    {"llm", "embedding", "rerank", "transcription", "speech", "image", "video"}
)
_MODALITIES = frozenset({"text", "image", "audio", "video"})


def normalize_model_provider(provider_id: str, value: dict[str, Any]) -> dict[str, Any]:
    """Validate one provider revision without resolving any credential."""
    normalized_id = str(provider_id).strip().lower()
    if not _PROVIDER_ID.fullmatch(normalized_id):
        raise ValueError("model provider id is invalid")
    if not isinstance(value, dict):
        raise ValueError("model provider configuration must be an object")
    allowed = {
        "enabled",
        "extension_id",
        "api_base",
        "api_key_ref",
        "allow_insecure_http",
        "credential_mode",
        "extra_header_refs",
        "request_timeout_seconds",
        "models",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("model provider contains unsupported fields: " + ", ".join(unknown))
    extension_id = str(value.get("extension_id") or "").strip()
    if not _EXTENSION_ID.fullmatch(extension_id):
        raise ValueError("model provider extension_id must identify a provider extension")
    allow_insecure = bool(value.get("allow_insecure_http", False))
    api_base = _normalize_api_base(value.get("api_base"), allow_insecure)
    credential_mode = str(value.get("credential_mode") or "api_key").strip()
    if credential_mode not in {"api_key", "none"}:
        raise ValueError("model provider credential_mode must be api_key or none")
    api_key_ref = str(value.get("api_key_ref") or "").strip()
    if credential_mode == "api_key" and not _ENV_REFERENCE.fullmatch(api_key_ref):
        raise ValueError("model provider api_key_ref must use env://VARIABLE")
    if credential_mode == "none" and api_key_ref:
        raise ValueError("credential-free model provider cannot define api_key_ref")
    if credential_mode == "none" and urlsplit(api_base).hostname not in _LOOPBACK_HOSTS:
        raise ValueError("credential-free model providers are loopback-only")
    timeout = float(value.get("request_timeout_seconds") or 120)
    if not 1 <= timeout <= 3600:
        raise ValueError("model provider request_timeout_seconds must be between 1 and 3600")
    headers = _header_references(value.get("extra_header_refs"))
    raw_models = value.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise ValueError("model provider must declare at least one model")
    models = [_normalize_model(normalized_id, item) for item in raw_models]
    identities = [item["model_id"] for item in models]
    if len(identities) != len(set(identities)):
        raise ValueError("model provider contains duplicate model ids")
    if not any(item["enabled"] for item in models):
        raise ValueError("model provider must contain at least one enabled model")
    return {
        "enabled": bool(value.get("enabled", True)),
        "extension_id": extension_id,
        "api_base": api_base,
        "api_key_ref": api_key_ref,
        "allow_insecure_http": allow_insecure,
        "credential_mode": credential_mode,
        "extra_header_refs": headers,
        "request_timeout_seconds": timeout,
        "models": models,
    }


def materialize_model_provider(configuration: dict[str, Any]) -> dict[str, Any]:
    """Resolve provider secrets only inside an Agent Worker."""
    value = dict(configuration)
    reference = str(value.pop("api_key_ref", "") or "")
    credential_mode = str(value.get("credential_mode") or "api_key")
    if credential_mode == "api_key":
        value["api_key"] = _resolve_reference(reference, "model provider API key")
    else:
        value["api_key"] = ""
    header_refs = dict(value.pop("extra_header_refs", {}) or {})
    value["extra_headers"] = {
        name: _resolve_reference(reference, f"model provider header {name}")
        for name, reference in header_refs.items()
    }
    return value


def model_provider_public(configuration: dict[str, Any]) -> dict[str, Any]:
    """Return a browser-safe representation containing references, never values."""
    value = dict(configuration)
    reference = str(value.get("api_key_ref") or "")
    matched = _ENV_REFERENCE.fullmatch(reference)
    value["api_key_variable"] = matched.group(1) if matched else ""
    value["extra_header_variables"] = {
        name: matched.group(1)
        for name, reference in dict(value.get("extra_header_refs") or {}).items()
        if (matched := _ENV_REFERENCE.fullmatch(str(reference))) is not None
    }
    return value


def model_provider_fingerprint(configuration: dict[str, Any]) -> str:
    body = json.dumps(
        configuration, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def validate_agent_model_policy(
    model_policy: dict[str, Any], models: list[dict[str, Any]]
) -> None:
    """Require an Agent policy to resolve against one active LLM catalog."""
    by_id = {
        str(item.get("model_id") or ""): item
        for item in models
        if item.get("enabled", True) and str(item.get("kind") or "llm") == "llm"
    }
    primary = str(model_policy.get("primary") or "").strip()
    fallbacks = [
        str(item).strip()
        for item in model_policy.get("fallbacks") or ()
        if str(item).strip()
    ]
    missing = [model_id for model_id in (primary, *fallbacks) if model_id not in by_id]
    if missing:
        raise ValueError(
            "Agent model policy references models outside the active catalog: "
            + ", ".join(dict.fromkeys(missing))
        )
    requested_tokens = int(model_policy.get("max_tokens") or 0)
    primary_limit = int(by_id[primary].get("max_output_tokens") or 0)
    if requested_tokens > 0 and primary_limit > 0 and requested_tokens > primary_limit:
        raise ValueError(
            f"Agent max_tokens {requested_tokens} exceeds {primary} output limit "
            f"{primary_limit}"
        )


def _normalize_api_base(value: Any, allow_insecure_http: bool) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("model provider api_base must be an absolute HTTP URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("model provider api_base cannot contain credentials, query, or fragment")
    if parsed.scheme == "http" and (
        not allow_insecure_http or parsed.hostname.lower() not in _LOOPBACK_HOSTS
    ):
        raise ValueError("model provider requires HTTPS; insecure HTTP is loopback-only")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _normalize_model(provider_id: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("model catalog item must be an object")
    model_id = str(raw.get("model_id") or "").strip()
    if not model_id or len(model_id) > 256 or not model_id.startswith(f"{provider_id}/"):
        raise ValueError(f"model id must use the exact {provider_id}/... prefix")
    kind = str(raw.get("kind") or "llm").strip().lower()
    if kind not in _MODEL_KINDS:
        raise ValueError(f"model {model_id} kind is unsupported")
    modalities = [str(item).strip().lower() for item in raw.get("input_modalities") or ["text"]]
    if not modalities or any(item not in _MODALITIES for item in modalities):
        raise ValueError(f"model {model_id} input modalities are invalid")
    context_window = int(raw.get("context_window") or 0)
    max_output_tokens = int(raw.get("max_output_tokens") or 0)
    if not 0 <= context_window <= 100_000_000:
        raise ValueError(f"model {model_id} context_window is invalid")
    if not 0 <= max_output_tokens <= 10_000_000:
        raise ValueError(f"model {model_id} max_output_tokens is invalid")
    temperature = float(raw.get("default_temperature", 0.3))
    if not 0 <= temperature <= 2:
        raise ValueError(f"model {model_id} default_temperature is invalid")
    tags = [str(item).strip() for item in raw.get("tags") or []]
    if any(not item for item in tags):
        raise ValueError(f"model {model_id} tags contain an empty value")
    pricing_keys = (
        "input_cost_per_million_tokens",
        "output_cost_per_million_tokens",
        "cached_input_cost_per_million_tokens",
        "cache_creation_input_cost_per_million_tokens",
    )
    pricing: dict[str, float | None] = {}
    for key in pricing_keys:
        value = raw.get(key)
        if value is None:
            pricing[key] = None
            continue
        price = float(value)
        if not 0 <= price <= 1_000_000:
            raise ValueError(f"model {model_id} {key} is invalid")
        pricing[key] = price
    if kind == "embedding" and any(
        pricing[key] is not None for key in pricing_keys if key != "input_cost_per_million_tokens"
    ):
        raise ValueError(f"embedding model {model_id} only supports input token pricing")
    if kind not in {"llm", "embedding"} and any(value is not None for value in pricing.values()):
        raise ValueError(f"model {model_id} kind does not use token pricing")
    return {
        "model_id": model_id,
        "name": str(raw.get("name") or model_id).strip()[:160],
        "description": str(raw.get("description") or "").strip()[:2000],
        "kind": kind,
        "enabled": bool(raw.get("enabled", True)),
        "input_modalities": list(dict.fromkeys(modalities)),
        "context_window": context_window,
        "max_output_tokens": max_output_tokens,
        "supports_tools": bool(raw.get("supports_tools", kind == "llm")),
        "supports_reasoning": bool(raw.get("supports_reasoning", False)),
        "supports_structured_output": bool(raw.get("supports_structured_output", False)),
        "default_temperature": temperature,
        "tags": list(dict.fromkeys(tags)),
        "dimensions": _embedding_dimensions(model_id, kind, raw.get("dimensions")),
        **pricing,
    }


def _embedding_dimensions(model_id: str, kind: str, value: Any) -> int:
    dimensions = int(value or 0)
    if kind == "embedding" and not 1 <= dimensions <= 16_000:
        raise ValueError(f"embedding model {model_id} dimensions must be between 1 and 16000")
    if kind != "embedding" and dimensions:
        raise ValueError(f"non-embedding model {model_id} cannot declare dimensions")
    return dimensions


def _header_references(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError("model provider extra_header_refs must be an object with <= 32 items")
    output: dict[str, str] = {}
    for raw_name, raw_reference in value.items():
        name = str(raw_name).strip()
        reference = str(raw_reference).strip()
        if not _HEADER_NAME.fullmatch(name) or not _ENV_REFERENCE.fullmatch(reference):
            raise ValueError("model provider headers require safe names and env:// references")
        output[name] = reference
    return output


def _resolve_reference(reference: str, label: str) -> str:
    matched = _ENV_REFERENCE.fullmatch(reference)
    if matched is None:
        raise ValueError(f"{label} reference is invalid")
    variable = matched.group(1)
    secret = os.environ.get(variable)
    if secret is None:
        raise ValueError(f"{label} environment variable is missing: {variable}")
    if not secret:
        raise ValueError(f"{label} environment variable is empty: {variable}")
    return secret


__all__ = [
    "materialize_model_provider",
    "model_provider_fingerprint",
    "model_provider_public",
    "normalize_model_provider",
    "validate_agent_model_policy",
]
