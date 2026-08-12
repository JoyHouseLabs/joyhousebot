"""Versioned control-plane definitions for remote Capability services."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from jsonschema import Draft202012Validator, SchemaError

_CONNECTION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_CAPABILITY_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENV_REFERENCE = re.compile(r"^env://([A-Za-z_][A-Za-z0-9_]*)$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_MAX_RESPONSE_BYTES = 50 * 1024 * 1024


def normalize_remote_connection(
    connection_id: str, value: dict[str, Any]
) -> dict[str, Any]:
    """Validate and normalize one secret-free remote service revision."""
    normalized_id = str(connection_id).strip()
    if not _CONNECTION_ID.fullmatch(normalized_id):
        raise ValueError("remote connection id is invalid")
    if not isinstance(value, dict):
        raise ValueError("remote connection configuration must be an object")
    allowed = {
        "enabled",
        "base_url",
        "key_id",
        "signing_secret_ref",
        "allow_insecure_http",
        "require_response_signature",
        "timeout_seconds",
        "max_response_bytes",
        "capabilities",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            "remote connection contains unsupported fields: " + ", ".join(unknown)
        )
    allow_insecure = bool(value.get("allow_insecure_http", False))
    base_url = _normalize_base_url(value.get("base_url"), allow_insecure)
    key_id = str(value.get("key_id") or "").strip()
    if not key_id or len(key_id) > 128:
        raise ValueError("remote connection key_id is required and must be <= 128 characters")
    secret_ref = str(value.get("signing_secret_ref") or "").strip()
    if not _ENV_REFERENCE.fullmatch(secret_ref):
        raise ValueError("remote connection signing_secret_ref must use env://VARIABLE")
    timeout = float(value.get("timeout_seconds") or 60)
    if not 1 <= timeout <= 3600:
        raise ValueError("remote connection timeout_seconds must be between 1 and 3600")
    max_response_bytes = int(value.get("max_response_bytes") or 10 * 1024 * 1024)
    if not 1024 <= max_response_bytes <= _MAX_RESPONSE_BYTES:
        raise ValueError("remote connection max_response_bytes is outside the safe range")
    raw_capabilities = value.get("capabilities")
    if not isinstance(raw_capabilities, list) or not raw_capabilities:
        raise ValueError("remote connection must declare at least one capability")
    capabilities = [_normalize_capability(item) for item in raw_capabilities]
    identities = [
        (str(item["capability_id"]), str(item["version"])) for item in capabilities
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("remote connection contains duplicate capability versions")
    return {
        "enabled": bool(value.get("enabled", True)),
        "base_url": base_url,
        "key_id": key_id,
        "signing_secret_ref": secret_ref,
        "allow_insecure_http": allow_insecure,
        "require_response_signature": bool(
            value.get("require_response_signature", True)
        ),
        "timeout_seconds": timeout,
        "max_response_bytes": max_response_bytes,
        "capabilities": capabilities,
    }


def remote_connection_fingerprint(configuration: dict[str, Any]) -> str:
    body = json.dumps(
        configuration,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def materialize_remote_connection(configuration: dict[str, Any]) -> dict[str, Any]:
    """Resolve an env reference only inside a Worker and never persist its value."""
    value = dict(configuration)
    reference = str(value.pop("signing_secret_ref", "")).strip()
    matched = _ENV_REFERENCE.fullmatch(reference)
    if matched is None:
        raise ValueError("remote connection signing secret reference is invalid")
    variable = matched.group(1)
    secret = os.environ.get(variable)
    if secret is None:
        raise ValueError(f"remote connection secret environment variable is missing: {variable}")
    if len(secret.encode("utf-8")) < 32:
        raise ValueError("remote connection signing secret must contain at least 32 bytes")
    value["signing_secret"] = secret
    return value


def remote_connection_public(configuration: dict[str, Any]) -> dict[str, Any]:
    """Return a browser-safe revision representation."""
    value = dict(configuration)
    reference = str(value.get("signing_secret_ref") or "")
    matched = _ENV_REFERENCE.fullmatch(reference)
    value["signing_secret_ref"] = reference
    value["signing_secret_variable"] = matched.group(1) if matched else ""
    return value


def _normalize_base_url(value: Any, allow_insecure_http: bool) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("remote connection base_url must be an absolute HTTP URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "remote connection base_url cannot contain credentials, query, or fragment"
        )
    if parsed.scheme == "http" and (
        not allow_insecure_http or parsed.hostname.lower() not in _LOOPBACK_HOSTS
    ):
        raise ValueError(
            "remote connection requires HTTPS; insecure HTTP is loopback-only"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _normalize_capability(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("remote capability declaration must be an object")
    capability_id = str(raw.get("capability_id") or "").strip()
    version = str(raw.get("version") or "").strip()
    digest = str(raw.get("implementation_digest") or "").strip()
    if not _CAPABILITY_ID.fullmatch(capability_id):
        raise ValueError("remote capability capability_id is invalid")
    if not version or len(version) > 128:
        raise ValueError(f"remote capability {capability_id} version is invalid")
    if not _DIGEST.fullmatch(digest):
        raise ValueError(
            f"remote capability {capability_id} implementation_digest must be sha256"
        )
    side_effect = str(raw.get("side_effect") or "none").strip().lower()
    idempotent = bool(raw.get("idempotent", side_effect in {"none", "read"}))
    if side_effect not in {"none", "read"} and not idempotent:
        raise ValueError(
            f"remote write capability {capability_id} must honor Runtime idempotency"
        )
    timeout = int(raw.get("timeout_seconds") or 60)
    if not 1 <= timeout <= 3600:
        raise ValueError(f"remote capability {capability_id} timeout is invalid")
    classification = str(raw.get("data_classification") or "confidential")
    if classification not in {"public", "internal", "confidential", "restricted"}:
        raise ValueError(
            f"remote capability {capability_id} data classification is invalid"
        )
    concurrency = str(
        raw.get("invocation_concurrency")
        or ("parallel_safe" if side_effect in {"none", "read"} else "sequential")
    )
    if concurrency not in {"parallel_safe", "sequential"}:
        raise ValueError(f"remote capability {capability_id} concurrency is invalid")
    max_concurrent = int(raw.get("max_concurrent_invocations") or 1)
    if not 1 <= max_concurrent <= 1000:
        raise ValueError(f"remote capability {capability_id} concurrency limit is invalid")
    input_schema = _schema(
        raw.get("input_schema"),
        default={"type": "object", "properties": {}},
        field_name=f"{capability_id} input_schema",
    )
    if input_schema.get("type", "object") != "object":
        raise ValueError(f"remote capability {capability_id} input schema must be an object")
    output_schema = _schema(
        raw.get("output_schema"), default={}, field_name=f"{capability_id} output_schema"
    )
    permissions = _strings(raw.get("permissions"), f"{capability_id} permissions")
    tags = _strings(raw.get("tags"), f"{capability_id} tags")
    return {
        "capability_id": capability_id,
        "version": version,
        "implementation_digest": digest,
        "name": str(raw.get("name") or capability_id).strip(),
        "description": str(raw.get("description") or capability_id).strip(),
        "input_schema": input_schema,
        "output_schema": output_schema,
        "permissions": permissions,
        "tags": tags,
        "side_effect": side_effect,
        "idempotent": idempotent,
        "retryable": bool(raw.get("retryable", idempotent)),
        "data_classification": classification,
        "timeout_seconds": timeout,
        "expected_duration_seconds": max(
            0, int(raw.get("expected_duration_seconds") or 10)
        ),
        "invocation_concurrency": concurrency,
        "max_concurrent_invocations": max_concurrent,
        "cost_policy": dict(raw.get("cost_policy") or {}),
    }


def _schema(value: Any, *, default: dict[str, Any], field_name: str) -> dict[str, Any]:
    result = dict(value) if isinstance(value, dict) else dict(default)
    try:
        Draft202012Validator.check_schema(result)
    except SchemaError as exc:
        raise ValueError(f"remote capability {field_name} is invalid: {exc.message}") from exc
    return result


def _strings(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"remote capability {field_name} must be an array")
    output = [str(item).strip() for item in value]
    if any(not item for item in output):
        raise ValueError(f"remote capability {field_name} contains an empty value")
    return output


__all__ = [
    "materialize_remote_connection",
    "normalize_remote_connection",
    "remote_connection_fingerprint",
    "remote_connection_public",
]
