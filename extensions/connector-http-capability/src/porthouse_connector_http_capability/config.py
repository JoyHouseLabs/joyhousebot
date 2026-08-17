"""Configuration, signing, and Extension Host preflight for the HTTP connector."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import rfc8785
from jsonschema import Draft202012Validator

from porthouse.extension_sdk.connectors import connector_settings
from porthouse.extension_sdk.network import sanitize_error_message
from porthouse.extension_sdk.tools import ToolInvocationError

_PROTOCOL_VERSION = "1"
_CAPABILITY_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SERVICE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_DEFAULT_TIMEOUT_SECONDS = 60.0
_DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_MAX_RESPONSE_BYTES = 50 * 1024 * 1024
_HOST_PROVENANCE_FIELDS = frozenset(
    {
        "host_id",
        "host_version",
        "host_build_digest",
        "host_extension_id",
        "host_extension_version",
        "host_extension_build_digest",
        "host_extension_lockfile_digest",
        "host_sdk_version",
    }
)

def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_request_body(
    *, method: str, path: str, timestamp: str, nonce: str, body: bytes, secret: str
) -> str:
    """Return the language-neutral v1 request signature."""
    body_digest = hashlib.sha256(body).hexdigest()
    canonical = "\n".join(
        (
            "JHBCAP-HMAC-SHA256",
            _PROTOCOL_VERSION,
            method.upper(),
            path,
            timestamp,
            nonce,
            body_digest,
        )
    ).encode("utf-8")
    return "v1=" + hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def sign_response_body(*, status_code: int, nonce: str, body: bytes, secret: str) -> str:
    """Bind a response body to the exact request nonce."""
    body_digest = hashlib.sha256(body).hexdigest()
    canonical = "\n".join(
        (
            "JHBCAP-RESPONSE-HMAC-SHA256",
            _PROTOCOL_VERSION,
            str(int(status_code)),
            nonce,
            body_digest,
        )
    ).encode("utf-8")
    return "v1=" + hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def request_digest(
    *,
    capability: dict[str, Any],
    subject: dict[str, Any],
    authorization: dict[str, Any],
    input_value: Any,
) -> str:
    """Return the RFC 8785 digest that freezes one remote invocation request."""
    projection = {
        "authorization": authorization,
        "capability": capability,
        "input": input_value,
        "subject": subject,
    }
    try:
        canonical = rfc8785.dumps(projection)
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as exc:
        raise ToolInvocationError(
            "REMOTE_INPUT_NOT_CANONICAL",
            "remote capability input cannot be represented as canonical JSON",
        ) from exc
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


@dataclass(frozen=True, slots=True)
class RemoteCapabilitySpec:
    capability_id: str
    version: str
    implementation_digest: str
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permissions: tuple[str, ...]
    tags: tuple[str, ...]
    side_effect: str
    idempotent: bool
    retryable: bool
    data_classification: str
    timeout_seconds: int
    expected_duration_seconds: int
    invocation_concurrency: str
    max_concurrent_invocations: int
    cost_policy: dict[str, Any]
    execution_mode: str
    supports_stream: bool
    provenance: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RemoteServiceConfig:
    service_id: str
    service_profile: str
    base_url: str
    key_id: str
    signing_secret: str
    timeout_seconds: float
    max_response_bytes: int
    require_response_signature: bool
    capabilities: tuple[RemoteCapabilitySpec, ...]
    host_protocol_version: str
    expected_host_manifest_digest: str
    require_host_preflight: bool


def _normalize_base_url(value: Any, *, allow_insecure_http: bool) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("remote capability base_url must be an absolute HTTP URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("remote capability base_url cannot contain credentials, query, or fragment")
    if parsed.scheme == "http" and (
        not allow_insecure_http or parsed.hostname.lower() not in _LOOPBACK_HOSTS
    ):
        raise ValueError(
            "remote capability base_url requires HTTPS; insecure HTTP is loopback-only"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _schema(value: Any, *, field_name: str, default: dict[str, Any]) -> dict[str, Any]:
    result = dict(value) if isinstance(value, dict) else dict(default)
    try:
        Draft202012Validator.check_schema(result)
    except Exception as exc:
        raise ValueError(f"remote capability {field_name} is not valid JSON Schema") from exc
    return result


def _string_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"remote capability {field_name} must be an array")
    result = tuple(str(item).strip() for item in value)
    if any(not item for item in result):
        raise ValueError(f"remote capability {field_name} cannot contain empty values")
    return result


def _capability_spec(raw: Any) -> RemoteCapabilitySpec:
    value = connector_settings(raw)
    capability_id = str(value.get("capability_id") or "").strip()
    version = str(value.get("version") or "").strip()
    digest = str(value.get("implementation_digest") or "").strip()
    if not _CAPABILITY_ID.fullmatch(capability_id):
        raise ValueError("remote capability capability_id is invalid")
    if not version or len(version) > 128:
        raise ValueError(f"remote capability {capability_id} version is invalid")
    if not _DIGEST.fullmatch(digest):
        raise ValueError(
            f"remote capability {capability_id} implementation_digest must be sha256"
        )
    side_effect = str(value.get("side_effect") or "none").strip().lower()
    idempotent = bool(value.get("idempotent", side_effect in {"none", "read"}))
    if side_effect not in {"none", "read"} and not idempotent:
        raise ValueError(
            f"remote write capability {capability_id} must honor the Runtime idempotency key"
        )
    timeout_seconds = int(value.get("timeout_seconds") or _DEFAULT_TIMEOUT_SECONDS)
    if not 1 <= timeout_seconds <= 3600:
        raise ValueError(f"remote capability {capability_id} timeout_seconds is invalid")
    data_classification = str(value.get("data_classification") or "confidential")
    if data_classification not in {"public", "internal", "confidential", "restricted"}:
        raise ValueError(f"remote capability {capability_id} data_classification is invalid")
    concurrency = str(
        value.get("invocation_concurrency")
        or ("parallel_safe" if side_effect in {"none", "read"} else "sequential")
    )
    if concurrency not in {"parallel_safe", "sequential"}:
        raise ValueError(f"remote capability {capability_id} concurrency is invalid")
    max_concurrent = int(value.get("max_concurrent_invocations") or 1)
    if not 1 <= max_concurrent <= 1000:
        raise ValueError(f"remote capability {capability_id} max concurrency is invalid")
    execution_mode = str(value.get("execution_mode") or "immediate").strip()
    if execution_mode not in {"immediate", "durable"}:
        raise ValueError(f"remote capability {capability_id} execution mode is invalid")
    raw_provenance = value.get("provenance") or {}
    if not isinstance(raw_provenance, dict):
        raise ValueError(f"remote capability {capability_id} provenance must be an object")
    unknown_provenance = sorted(set(raw_provenance) - _HOST_PROVENANCE_FIELDS)
    if unknown_provenance:
        raise ValueError(
            f"remote capability {capability_id} provenance contains unsupported fields: "
            + ", ".join(unknown_provenance)
        )
    return RemoteCapabilitySpec(
        capability_id=capability_id,
        version=version,
        implementation_digest=digest,
        name=str(value.get("name") or capability_id).strip(),
        description=str(value.get("description") or capability_id).strip(),
        input_schema=_schema(
            value.get("input_schema"),
            field_name="input_schema",
            default={"type": "object", "properties": {}},
        ),
        output_schema=_schema(
            value.get("output_schema"), field_name="output_schema", default={}
        ),
        permissions=_string_tuple(value.get("permissions"), field_name="permissions"),
        tags=_string_tuple(value.get("tags"), field_name="tags"),
        side_effect=side_effect,
        idempotent=idempotent,
        retryable=bool(value.get("retryable", idempotent)),
        data_classification=data_classification,
        timeout_seconds=timeout_seconds,
        expected_duration_seconds=max(0, int(value.get("expected_duration_seconds") or 10)),
        invocation_concurrency=concurrency,
        max_concurrent_invocations=max_concurrent,
        cost_policy=dict(value.get("cost_policy") or {}),
        execution_mode=execution_mode,
        supports_stream=bool(value.get("supports_stream", False)),
        provenance=dict(raw_provenance),
    )


def _service_config(service_id: str, raw: Any) -> RemoteServiceConfig:
    if not _SERVICE_ID.fullmatch(service_id):
        raise ValueError(f"remote capability service id {service_id!r} is invalid")
    value = connector_settings(raw)
    service_profile = str(value.get("service_profile") or "").strip()
    if service_profile not in {"business", "extension_host"}:
        raise ValueError(
            f"remote capability service {service_id} has an invalid service_profile"
        )
    base_url = _normalize_base_url(
        value.get("base_url"),
        allow_insecure_http=bool(value.get("allow_insecure_http", False)),
    )
    key_id = str(value.get("key_id") or "").strip()
    secret = str(value.get("signing_secret") or "")
    if not key_id or len(key_id) > 128 or len(secret.encode("utf-8")) < 32:
        raise ValueError(
            f"remote capability service {service_id} requires key_id and a 32-byte signing_secret"
        )
    timeout = float(value.get("timeout_seconds") or _DEFAULT_TIMEOUT_SECONDS)
    max_bytes = int(value.get("max_response_bytes") or _DEFAULT_MAX_RESPONSE_BYTES)
    if not 1 <= timeout <= 3600:
        raise ValueError(f"remote capability service {service_id} timeout is invalid")
    if not 1024 <= max_bytes <= _MAX_RESPONSE_BYTES:
        raise ValueError(f"remote capability service {service_id} response limit is invalid")
    capabilities_raw = value.get("capabilities")
    if not isinstance(capabilities_raw, list) or not capabilities_raw:
        raise ValueError(f"remote capability service {service_id} has no capabilities")
    capabilities = tuple(_capability_spec(item) for item in capabilities_raw)
    identities = [(item.capability_id, item.version) for item in capabilities]
    if len(set(identities)) != len(identities):
        raise ValueError(f"remote capability service {service_id} contains duplicate versions")
    host_protocol_version = str(value.get("host_protocol_version") or "").strip()
    expected_host_manifest_digest = str(
        value.get("expected_host_manifest_digest") or ""
    ).strip()
    require_host_preflight = bool(value.get("require_host_preflight", False))
    if service_profile == "extension_host":
        if host_protocol_version != _PROTOCOL_VERSION:
            raise ValueError(
                f"extension host {service_id} requires protocol version {_PROTOCOL_VERSION}"
            )
        if not _DIGEST.fullmatch(expected_host_manifest_digest):
            raise ValueError(
                f"extension host {service_id} requires an exact manifest digest"
            )
        if not require_host_preflight:
            raise ValueError(f"extension host {service_id} preflight cannot be disabled")
    elif host_protocol_version or expected_host_manifest_digest or require_host_preflight:
        raise ValueError(
            f"business service {service_id} cannot declare extension host preflight fields"
        )
    return RemoteServiceConfig(
        service_id=service_id,
        service_profile=service_profile,
        base_url=base_url,
        key_id=key_id,
        signing_secret=secret,
        timeout_seconds=timeout,
        max_response_bytes=max_bytes,
        require_response_signature=bool(value.get("require_response_signature", True)),
        capabilities=capabilities,
        host_protocol_version=host_protocol_version,
        expected_host_manifest_digest=expected_host_manifest_digest,
        require_host_preflight=require_host_preflight,
    )


def _manifest_capability(spec: RemoteCapabilitySpec) -> dict[str, Any]:
    return {
        "capability_id": spec.capability_id,
        "version": spec.version,
        "implementation_digest": spec.implementation_digest,
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.input_schema,
        "output_schema": spec.output_schema,
        "permissions": list(spec.permissions),
        "tags": list(spec.tags),
        "side_effect": spec.side_effect,
        "idempotent": spec.idempotent,
        "retryable": spec.retryable,
        "data_classification": spec.data_classification,
        "timeout_seconds": spec.timeout_seconds,
        "expected_duration_seconds": spec.expected_duration_seconds,
        "invocation_concurrency": spec.invocation_concurrency,
        "max_concurrent_invocations": spec.max_concurrent_invocations,
        "cost_policy": spec.cost_policy,
        "execution_mode": spec.execution_mode,
        "supports_stream": spec.supports_stream,
        "provenance": spec.provenance,
    }


def extension_host_manifest_digest(manifest: dict[str, Any]) -> str:
    """Return the RFC 8785 digest that identifies one immutable Host manifest."""
    try:
        canonical = rfc8785.dumps(manifest)
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as exc:
        raise RuntimeError("extension host manifest is not canonical JSON") from exc
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


async def _read_bounded_response(
    response: httpx.Response, *, max_response_bytes: int
) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise RuntimeError("extension host returned an invalid Content-Length") from exc
        if declared_size < 0 or declared_size > max_response_bytes:
            raise RuntimeError("extension host response exceeds configured limit")
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_response_bytes:
            raise RuntimeError("extension host response exceeds configured limit")
        chunks.append(chunk)
    return b"".join(chunks)


@dataclass(frozen=True, slots=True)
class _PreflightResponse:
    status_code: int
    headers: dict[str, str]
    nonce: str
    body: bytes


async def preflight_extension_host(
    service_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """Verify the signed, immutable Host manifest without invoking a Capability."""
    service = _service_config(service_id, raw)
    if service.service_profile != "extension_host":
        raise ValueError("host preflight is only available for extension_host services")
    response = await _request_host_manifest(service)
    value = _verified_preflight_payload(service, response)
    actual_digest, host, extensions = _verified_host_manifest(service, value)
    return {
        "manifest_digest": actual_digest,
        "host": host,
        "extensions": extensions,
        "runtime": value.get("runtime") or {},
    }


async def _request_host_manifest(service: RemoteServiceConfig) -> _PreflightResponse:
    relative_path = "/meta:describe"
    base_path = urlsplit(service.base_url).path.rstrip("/")
    signed_path = f"{base_path}{relative_path}" or "/"
    timestamp = str(int(time.time()))
    nonce = uuid4().hex
    payload = {
        "protocol_version": _PROTOCOL_VERSION,
        "request": {
            "service_id": service.service_id,
            "expected_manifest_digest": service.expected_host_manifest_digest,
        },
    }
    body = _canonical_json(payload)
    signature = sign_request_body(
        method="POST",
        path=signed_path,
        timestamp=timestamp,
        nonce=nonce,
        body=body,
        secret=service.signing_secret,
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Porthouse-Capability-Protocol": _PROTOCOL_VERSION,
        "X-Porthouse-Key-Id": service.key_id,
        "X-Porthouse-Timestamp": timestamp,
        "X-Porthouse-Nonce": nonce,
        "X-Porthouse-Signature": signature,
        "X-Porthouse-Run-ID": f"preflight:{service.service_id}",
        "Idempotency-Key": f"preflight:{service.expected_host_manifest_digest}",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(service.timeout_seconds), follow_redirects=False
        ) as client:
            request = client.build_request(
                "POST", f"{service.base_url}{relative_path}", headers=headers, content=body
            )
            response = await client.send(request, stream=True)
            try:
                raw_response = await _read_bounded_response(
                    response, max_response_bytes=service.max_response_bytes
                )
            finally:
                await response.aclose()
    except httpx.TimeoutException as exc:
        raise RuntimeError("extension host preflight timed out") from exc
    except httpx.RequestError as exc:
        raise RuntimeError(
            f"extension host preflight connection failed: {sanitize_error_message(str(exc))}"
        ) from exc
    return _PreflightResponse(
        status_code=response.status_code,
        headers=dict(response.headers),
        nonce=nonce,
        body=raw_response,
    )


def _verified_preflight_payload(
    service: RemoteServiceConfig, response: _PreflightResponse
) -> dict[str, Any]:
    if service.require_response_signature:
        expected_signature = sign_response_body(
            status_code=response.status_code,
            nonce=response.nonce,
            body=response.body,
            secret=service.signing_secret,
        )
        received = str(response.headers.get("x-porthouse-response-signature") or "")
        if not received or not hmac.compare_digest(received, expected_signature):
            raise RuntimeError("extension host preflight response signature is invalid")
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("extension host preflight returned invalid JSON") from exc
    if not isinstance(value, dict) or not 200 <= response.status_code < 300:
        raise RuntimeError("extension host preflight request failed")
    if value.get("status") != "succeeded":
        raise RuntimeError("extension host preflight did not succeed")
    if str(value.get("protocol_version") or "") != service.host_protocol_version:
        raise RuntimeError("extension host protocol version does not match the revision")
    return value


def _verified_host_manifest(
    service: RemoteServiceConfig, value: dict[str, Any]
) -> tuple[str, dict[str, Any], list[Any]]:
    manifest = value.get("manifest")
    if not isinstance(manifest, dict):
        raise RuntimeError("extension host did not return a manifest")
    if str(manifest.get("host_protocol_version") or "") != service.host_protocol_version:
        raise RuntimeError("extension host manifest protocol does not match the revision")
    actual_digest = extension_host_manifest_digest(manifest)
    if str(value.get("manifest_digest") or "") != actual_digest:
        raise RuntimeError("extension host self-reported manifest digest is invalid")
    if actual_digest != service.expected_host_manifest_digest:
        raise RuntimeError("extension host manifest digest does not match the revision")
    host = manifest.get("host")
    extensions = manifest.get("extensions")
    capabilities = manifest.get("capabilities")
    _verify_host_identity(host)
    _verify_extension_identities(extensions)
    _verify_host_capabilities(service, capabilities)
    return actual_digest, host, extensions


def _verify_host_identity(host: Any) -> None:
    if not isinstance(host, dict):
        raise RuntimeError("extension host manifest identity is incomplete")
    if not all(
        str(host.get(field) or "").strip()
        for field in ("host_id", "version", "build_digest")
    ) or not _DIGEST.fullmatch(str(host.get("build_digest") or "")):
        raise RuntimeError("extension host build identity is invalid")


def _verify_extension_identities(extensions: Any) -> None:
    if not isinstance(extensions, list):
        raise RuntimeError("extension host manifest identity is incomplete")
    for extension in extensions:
        if not isinstance(extension, dict) or not all(
            str(extension.get(field) or "").strip()
            for field in (
                "extension_id",
                "version",
                "build_digest",
                "lockfile_digest",
                "sdk_version",
            )
        ):
            raise RuntimeError("extension host package identity is incomplete")
        if not _DIGEST.fullmatch(str(extension["build_digest"])) or not _DIGEST.fullmatch(
            str(extension["lockfile_digest"])
        ):
            raise RuntimeError("extension host package digest is invalid")


def _verify_host_capabilities(
    service: RemoteServiceConfig, capabilities: Any
) -> None:
    if not isinstance(capabilities, list):
        raise RuntimeError("extension host manifest has no capability catalog")
    described = [_manifest_capability(_capability_spec(item)) for item in capabilities]
    expected_capabilities = [_manifest_capability(item) for item in service.capabilities]
    if described != expected_capabilities:
        raise RuntimeError("extension host capability definitions do not match the revision")
