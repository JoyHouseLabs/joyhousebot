"""Expose separately deployed business applications as governed capabilities."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import httpx
import rfc8785
from jsonschema import Draft202012Validator, ValidationError

from joyhousebot.extension_sdk import (
    Artifact,
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
    ExtensionManifest,
    OperationProgressEvent,
    OperationReconciliationResult,
    ToolConnectorConnectRequest,
    ToolConnectorExtension,
)
from joyhousebot.extension_sdk.connectors import connector_settings
from joyhousebot.extension_sdk.manifest import source_tree_digest
from joyhousebot.extension_sdk.network import sanitize_error_message
from joyhousebot.extension_sdk.tools import (
    InvocationStatus,
    Tool,
    ToolInvocationError,
    ToolOutput,
)

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
_BUILD_DIGEST = source_tree_digest(__file__)

HTTP_CAPABILITY_CONNECTOR_MANIFEST = ExtensionManifest(
    extension_id="connector-http-capability",
    version="0.1.0",
    name="JoyhouseBot HTTP Capability Connector",
    extension_types=("tool_connector",),
    description=(
        "Invoke versioned capabilities implemented by separately deployed business applications."
    ),
    distribution_name="joyhousebot-connector-http-capability",
    build_digest=_BUILD_DIGEST,
    required_permissions=("connector.http.invoke",),
    dependencies=(
        {"id": "remote-capability-service", "kind": "service", "required": True},
        {"id": "request-signing-secret", "kind": "credential", "required": True},
    ),
    configuration_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["services"],
        "properties": {
            "services": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "required": [
                        "base_url",
                        "key_id",
                        "signing_secret",
                        "capabilities",
                    ],
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "service_profile": {
                            "type": "string",
                            "enum": ["business", "extension_host"],
                        },
                        "base_url": {"type": "string"},
                        "key_id": {"type": "string"},
                        "signing_secret": {"type": "string"},
                        "allow_insecure_http": {"type": "boolean"},
                        "require_response_signature": {"type": "boolean"},
                        "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 3600},
                        "max_response_bytes": {
                            "type": "integer",
                            "minimum": 1024,
                            "maximum": _MAX_RESPONSE_BYTES,
                        },
                        "host_protocol_version": {"type": "string"},
                        "expected_host_manifest_digest": {"type": "string"},
                        "require_host_preflight": {"type": "boolean"},
                        "capabilities": {"type": "array", "items": {"type": "object"}},
                    },
                    "additionalProperties": False,
                },
            }
        },
    },
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


async def preflight_extension_host(
    service_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """Verify the signed, immutable Host manifest without invoking a Capability."""
    service = _service_config(service_id, raw)
    if service.service_profile != "extension_host":
        raise ValueError("host preflight is only available for extension_host services")
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
        "X-Joyhouse-Capability-Protocol": _PROTOCOL_VERSION,
        "X-Joyhouse-Key-Id": service.key_id,
        "X-Joyhouse-Timestamp": timestamp,
        "X-Joyhouse-Nonce": nonce,
        "X-Joyhouse-Signature": signature,
        "X-Joyhouse-Run-ID": f"preflight:{service.service_id}",
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
    if service.require_response_signature:
        expected_signature = sign_response_body(
            status_code=response.status_code,
            nonce=nonce,
            body=raw_response,
            secret=service.signing_secret,
        )
        received = str(response.headers.get("X-Joyhouse-Response-Signature") or "")
        if not received or not hmac.compare_digest(received, expected_signature):
            raise RuntimeError("extension host preflight response signature is invalid")
    try:
        value = json.loads(raw_response)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("extension host preflight returned invalid JSON") from exc
    if not isinstance(value, dict) or not 200 <= response.status_code < 300:
        raise RuntimeError("extension host preflight request failed")
    if value.get("status") != "succeeded":
        raise RuntimeError("extension host preflight did not succeed")
    if str(value.get("protocol_version") or "") != service.host_protocol_version:
        raise RuntimeError("extension host protocol version does not match the revision")
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
    if not isinstance(host, dict) or not isinstance(extensions, list):
        raise RuntimeError("extension host manifest identity is incomplete")
    if not all(
        str(host.get(field) or "").strip()
        for field in ("host_id", "version", "build_digest")
    ) or not _DIGEST.fullmatch(str(host.get("build_digest") or "")):
        raise RuntimeError("extension host build identity is invalid")
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
    if not isinstance(capabilities, list):
        raise RuntimeError("extension host manifest has no capability catalog")
    described = [_manifest_capability(_capability_spec(item)) for item in capabilities]
    expected_capabilities = [_manifest_capability(item) for item in service.capabilities]
    if described != expected_capabilities:
        raise RuntimeError("extension host capability definitions do not match the revision")
    return {
        "manifest_digest": actual_digest,
        "host": host,
        "extensions": extensions,
        "runtime": value.get("runtime") or {},
    }


def _definition(service: RemoteServiceConfig, spec: RemoteCapabilitySpec) -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=CapabilityRef(
            spec.capability_id,
            spec.version,
            CapabilityKind.CONNECTOR,
            HTTP_CAPABILITY_CONNECTOR_MANIFEST.extension_id,
            HTTP_CAPABILITY_CONNECTOR_MANIFEST.version,
            HTTP_CAPABILITY_CONNECTOR_MANIFEST.build_digest,
        ),
        name=spec.name,
        description=spec.description,
        input_schema=spec.input_schema,
        output_schema=spec.output_schema,
        adapter="tool_connector:http-capability-v1",
        tags=(*spec.tags, "remote-capability", f"service:{service.service_id}"),
        execution_mode=spec.execution_mode,
        expected_duration_seconds=spec.expected_duration_seconds,
        timeout_seconds=spec.timeout_seconds,
        idempotent=spec.idempotent,
        retryable=spec.retryable,
        side_effect=spec.side_effect,
        invocation_concurrency=spec.invocation_concurrency,
        max_concurrent_invocations=spec.max_concurrent_invocations,
        supports_stream=spec.supports_stream,
        permissions=spec.permissions,
        data_classification=spec.data_classification,
        connection_ids=(service.service_id,),
        cost_policy=spec.cost_policy,
        origin={
            **spec.provenance,
            "extension_id": HTTP_CAPABILITY_CONNECTOR_MANIFEST.extension_id,
            "extension_version": HTTP_CAPABILITY_CONNECTOR_MANIFEST.version,
            "extension_build_digest": HTTP_CAPABILITY_CONNECTOR_MANIFEST.build_digest,
            "remote_service_id": service.service_id,
            "remote_implementation_digest": spec.implementation_digest,
            "service_profile": service.service_profile,
        },
    )


def _artifact(value: Any) -> Artifact:
    if not isinstance(value, dict):
        raise ToolInvocationError("REMOTE_RESPONSE_INVALID", "remote artifact must be an object")
    try:
        return Artifact(
            artifact_id=str(value.get("artifact_id") or f"artifact_{uuid4().hex}"),
            artifact_type=str(value["artifact_type"]),
            operation=str(value.get("operation") or "create"),
            schema_version=int(value.get("schema_version") or 1),
            media_type=str(value.get("media_type") or "application/json"),
            data=value.get("data"),
            uri=(str(value["uri"]) if value.get("uri") else None),
            content_sha256=str(value.get("content_sha256") or ""),
            object_version=str(value.get("object_version") or ""),
            provenance=dict(value.get("provenance") or {}),
            evidence=dict(value.get("evidence") or {}),
            metadata=dict(value.get("metadata") or {}),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolInvocationError(
            "REMOTE_RESPONSE_INVALID", "remote artifact contract is invalid"
        ) from exc


class RemoteCapabilityTool(Tool):
    """One immutable remote capability behind Runtime governance and signed HTTP."""

    supports_reconciliation = True

    def __init__(
        self,
        service: RemoteServiceConfig,
        spec: RemoteCapabilitySpec,
        client: httpx.AsyncClient,
        *,
        clock: Callable[[], float] = time.time,
        nonce_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self.service = service
        self.spec = spec
        self.client = client
        self._clock = clock
        self._nonce_factory = nonce_factory

    @property
    def name(self) -> str:
        return self.spec.capability_id

    @property
    def description(self) -> str:
        return self.spec.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self.spec.input_schema

    async def execute(self, **kwargs: Any) -> ToolOutput:
        context = kwargs.pop("tool_context", None)
        if context is None:
            raise ToolInvocationError(
                "REMOTE_CONTEXT_REQUIRED", "remote capability requires Runtime context"
            )
        capability = self._capability_identity()
        subject = {
            "user_id": context.user_id,
            "agent_id": context.agent_id,
            "session_id": context.session_id or context.session_key,
        }
        authorization = {
            "permissions": sorted(context.granted_permissions),
            "permission_mode": context.permission_mode,
        }
        frozen_request_digest = request_digest(
            capability=capability,
            subject=subject,
            authorization=authorization,
            input_value=kwargs,
        )
        payload = {
            "protocol_version": _PROTOCOL_VERSION,
            "capability": capability,
            "subject": subject,
            "execution": self._execution_identity(
                context, request_digest_value=frozen_request_digest
            ),
            "authorization": authorization,
            "input": kwargs,
        }
        path = f"/capabilities/{quote(self.spec.capability_id, safe='')}:invoke"
        response = await self._request(path, payload, context=context)
        return self._tool_output(
            response,
            context,
            request_digest_value=frozen_request_digest,
        )

    async def reconcile_operation(
        self, operation: dict[str, Any], **kwargs: Any
    ) -> OperationReconciliationResult:
        context = kwargs.get("tool_context")
        if context is None:
            raise ToolInvocationError(
                "REMOTE_CONTEXT_REQUIRED", "remote reconciliation requires Runtime context"
            )
        mismatch = self._operation_mismatch(operation, context)
        if mismatch:
            return OperationReconciliationResult(status="unknown", summary=mismatch)
        payload = {
            "protocol_version": _PROTOCOL_VERSION,
            "capability": self._capability_identity(),
            "subject": {
                "user_id": context.user_id,
                "agent_id": context.agent_id,
                "session_id": context.session_id or context.session_key,
            },
            "execution": self._execution_identity(
                context,
                request_digest_value=str(operation.get("request_digest") or "") or None,
            ),
            "operation": {
                "operation_id": str(operation["remote_operation_id"]),
                **(
                    {"cursor": str(operation["provider_cursor"])}
                    if operation.get("provider_cursor") is not None
                    else {}
                ),
            },
        }
        value = await self._request("/operations:reconcile", payload, context=context)
        status = str(value.get("status") or "unknown")
        if status not in {"pending", "succeeded", "failed", "unknown"}:
            raise ToolInvocationError(
                "REMOTE_RESPONSE_INVALID", "remote reconciliation status is invalid"
            )
        next_operation = self._operation_descriptor(
            context,
            operation_id=str(
                (value.get("operation") or {}).get("operation_id")
                or operation["remote_operation_id"]
            ),
            request_digest_value=str(operation.get("request_digest") or "") or None,
        )
        observation = self._operation_observation(value)
        if status == "pending":
            retry_after = value.get("retry_after_seconds")
            return OperationReconciliationResult(
                status="pending",
                summary=str(value.get("summary") or "remote operation is pending"),
                operation=next_operation,
                retry_after_seconds=(int(retry_after) if retry_after is not None else None),
                **observation,
            )
        if status == "unknown":
            return OperationReconciliationResult(
                status="unknown",
                summary=str(value.get("summary") or "remote operation outcome is unknown"),
                operation=next_operation,
                **observation,
            )
        if status == "failed":
            return OperationReconciliationResult(
                status="failed",
                summary=str(value.get("summary") or "remote operation failed"),
                error=self._error(value),
                operation=next_operation,
                **observation,
            )
        output = value.get("output")
        self._validate_output(output)
        return OperationReconciliationResult(
            status="succeeded",
            summary=str(value.get("summary") or "remote operation completed"),
            output=output,
            artifacts=[self._remote_artifact(item) for item in value.get("artifacts") or []],
            operation=next_operation,
            **observation,
        )

    @staticmethod
    def _operation_observation(value: dict[str, Any]) -> dict[str, Any]:
        raw_events = value.get("events") or []
        if not isinstance(raw_events, list):
            raise ToolInvocationError(
                "REMOTE_RESPONSE_INVALID", "remote operation events must be an array"
            )
        events: list[OperationProgressEvent] = []
        for item in raw_events:
            if not isinstance(item, dict) or not isinstance(item.get("payload", {}), dict):
                raise ToolInvocationError(
                    "REMOTE_RESPONSE_INVALID", "remote operation event is invalid"
                )
            try:
                events.append(
                    OperationProgressEvent(
                        event_id=str(item.get("event_id") or ""),
                        sequence=int(item.get("sequence")),
                        event_type=str(item.get("event_type") or ""),
                        summary=str(item.get("summary") or ""),
                        payload=dict(item.get("payload") or {}),
                        created_at=(
                            str(item["created_at"]) if item.get("created_at") else None
                        ),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise ToolInvocationError(
                    "REMOTE_RESPONSE_INVALID", "remote operation event is invalid"
                ) from exc
        cursor = value.get("provider_cursor")
        checkpoint = value.get("checkpoint_ref")
        progress_summary = value.get("progress_summary")
        progress_percent = value.get("progress_percent")
        return {
            "provider_cursor": str(cursor) if cursor is not None else None,
            "checkpoint_ref": str(checkpoint) if checkpoint is not None else None,
            "progress_summary": (
                str(progress_summary) if progress_summary is not None else None
            ),
            "progress_percent": (
                float(progress_percent) if progress_percent is not None else None
            ),
            "events": events,
            "cursor_reset": bool(value.get("cursor_reset", False)),
        }

    def _capability_identity(self) -> dict[str, str]:
        return {
            "capability_id": self.spec.capability_id,
            "version": self.spec.version,
            "implementation_digest": self.spec.implementation_digest,
        }

    @staticmethod
    def _execution_identity(
        context: Any, *, request_digest_value: str | None = None
    ) -> dict[str, Any]:
        value = {
            "run_id": context.run_id,
            "root_run_id": context.root_run_id or context.run_id,
            "task_id": context.task_id,
            "request_id": context.request_id,
            "action_id": context.action_id,
            "idempotency_key": context.idempotency_key,
        }
        if request_digest_value:
            value["request_digest"] = request_digest_value
        return value

    async def _request(
        self, relative_path: str, payload: dict[str, Any], *, context: Any
    ) -> dict[str, Any]:
        body = _canonical_json(payload)
        base_path = urlsplit(self.service.base_url).path.rstrip("/")
        signed_path = f"{base_path}{relative_path}" or "/"
        url = f"{self.service.base_url}{relative_path}"
        timestamp = str(int(self._clock()))
        nonce = str(self._nonce_factory())
        signature = sign_request_body(
            method="POST",
            path=signed_path,
            timestamp=timestamp,
            nonce=nonce,
            body=body,
            secret=self.service.signing_secret,
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Joyhouse-Capability-Protocol": _PROTOCOL_VERSION,
            "X-Joyhouse-Key-Id": self.service.key_id,
            "X-Joyhouse-Timestamp": timestamp,
            "X-Joyhouse-Nonce": nonce,
            "X-Joyhouse-Signature": signature,
            "X-Joyhouse-Run-ID": str(context.run_id),
            "Idempotency-Key": str(context.idempotency_key or ""),
        }
        if context.action_id:
            headers["X-Joyhouse-Action-ID"] = str(context.action_id)
        try:
            request = self.client.build_request("POST", url, headers=headers, content=body)
            response = await self.client.send(request, stream=True)
            try:
                raw = await self._read_response(response)
            finally:
                await response.aclose()
        except httpx.TimeoutException as exc:
            raise ToolInvocationError(
                "REMOTE_TIMEOUT", "remote capability request timed out", retryable=True
            ) from exc
        except httpx.RequestError as exc:
            raise ToolInvocationError(
                "REMOTE_CONNECTION_FAILED",
                sanitize_error_message(str(exc)),
                retryable=True,
            ) from exc
        if self.service.require_response_signature:
            expected = sign_response_body(
                status_code=response.status_code,
                nonce=nonce,
                body=raw,
                secret=self.service.signing_secret,
            )
            received = str(response.headers.get("X-Joyhouse-Response-Signature") or "")
            if not received or not hmac.compare_digest(received, expected):
                raise ToolInvocationError(
                    "REMOTE_RESPONSE_SIGNATURE_INVALID",
                    "remote capability response signature is missing or invalid",
                )
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolInvocationError(
                "REMOTE_RESPONSE_INVALID", "remote capability returned invalid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise ToolInvocationError(
                "REMOTE_RESPONSE_INVALID", "remote capability response must be an object"
            )
        if str(value.get("protocol_version") or "") != _PROTOCOL_VERSION:
            raise ToolInvocationError(
                "REMOTE_PROTOCOL_MISMATCH", "remote capability protocol version is unsupported"
            )
        if not 200 <= response.status_code < 300:
            error = self._error(value)
            raise ToolInvocationError(
                str(error["code"]),
                str(error["message"]),
                retryable=bool(error.get("retryable")),
            )
        return value

    async def _read_response(self, response: httpx.Response) -> bytes:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError as exc:
                raise ToolInvocationError(
                    "REMOTE_RESPONSE_INVALID",
                    "remote capability returned an invalid Content-Length",
                ) from exc
            if declared_size < 0:
                raise ToolInvocationError(
                    "REMOTE_RESPONSE_INVALID",
                    "remote capability returned an invalid Content-Length",
                )
            if declared_size > self.service.max_response_bytes:
                raise ToolInvocationError(
                    "REMOTE_RESPONSE_TOO_LARGE",
                    "remote capability response exceeds configured limit",
                )
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self.service.max_response_bytes:
                raise ToolInvocationError(
                    "REMOTE_RESPONSE_TOO_LARGE",
                    "remote capability response exceeds configured limit",
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _tool_output(
        self,
        value: dict[str, Any],
        context: Any,
        *,
        request_digest_value: str | None = None,
    ) -> ToolOutput:
        status = str(value.get("status") or "succeeded")
        if status == "failed":
            error = self._error(value)
            raise ToolInvocationError(
                str(error["code"]),
                str(error["message"]),
                retryable=bool(error.get("retryable")),
            )
        if status not in {"succeeded", "accepted"}:
            raise ToolInvocationError(
                "REMOTE_RESPONSE_INVALID", "remote capability status is invalid"
            )
        output = value.get("output")
        if status == "succeeded":
            self._validate_output(output)
        operation_value = value.get("operation") or {}
        operation_id = str(operation_value.get("operation_id") or "").strip()
        if status == "accepted" and not operation_id:
            raise ToolInvocationError(
                "REMOTE_RESPONSE_INVALID", "accepted remote operation requires operation_id"
            )
        requires_receipt = self.spec.side_effect not in {"none", "read"}
        receipt = value.get("write_receipt")
        if requires_receipt:
            if not isinstance(receipt, dict):
                raise ToolInvocationError(
                    "WRITE_RECEIPT_REQUIRED", "remote write did not return a write receipt"
                )
            if (
                str(receipt.get("action_id") or "") != str(context.action_id or "")
                or str(receipt.get("idempotency_key") or "")
                != str(context.idempotency_key or "")
            ):
                raise ToolInvocationError(
                    "WRITE_IDENTITY_MISMATCH",
                    "remote write receipt does not match the frozen Runtime Action",
                )
        operation = None
        if status == "accepted" or requires_receipt:
            operation = self._operation_descriptor(
                context,
                operation_id=operation_id or None,
                request_digest_value=request_digest_value,
            )
        summary = str(value.get("summary") or "remote capability completed")
        data = {"output": output}
        if isinstance(value.get("usage"), dict):
            data["remote_usage"] = dict(value["usage"])
        return ToolOutput(
            content=summary if output is None else json.dumps(output, ensure_ascii=False),
            summary=summary,
            data=data,
            artifacts=tuple(
                self._remote_artifact(item).to_dict()
                for item in value.get("artifacts") or []
            ),
            operation=operation,
            status=(
                InvocationStatus.ACCEPTED
                if status == "accepted"
                else InvocationStatus.SUCCEEDED
            ),
        )

    def _remote_artifact(self, value: Any) -> Artifact:
        artifact = _artifact(value)
        if self.service.service_profile == "extension_host" and artifact.uri:
            raise ToolInvocationError(
                "HOST_ARTIFACT_GRANT_REQUIRED",
                "Extension Host URI artifacts require a Runtime upload grant",
            )
        return artifact

    def _operation_descriptor(
        self,
        context: Any,
        *,
        operation_id: str | None,
        request_digest_value: str | None = None,
    ) -> dict[str, Any]:
        value = {
            "service_id": self.service.service_id,
            "capability_id": self.spec.capability_id,
            "capability_version": self.spec.version,
            "implementation_digest": self.spec.implementation_digest,
            "action_id": context.action_id,
            "idempotency_key": context.idempotency_key,
        }
        if operation_id:
            value["remote_operation_id"] = operation_id
        if request_digest_value:
            value["request_digest"] = request_digest_value
        return value

    def _operation_mismatch(self, operation: dict[str, Any], context: Any) -> str:
        expected = {
            "service_id": self.service.service_id,
            "capability_id": self.spec.capability_id,
            "capability_version": self.spec.version,
            "implementation_digest": self.spec.implementation_digest,
            "action_id": context.action_id,
            "idempotency_key": context.idempotency_key,
        }
        for key, value in expected.items():
            if operation.get(key) != value:
                return f"remote operation {key} does not match its frozen invocation"
        frozen_request_digest = str(operation.get("request_digest") or "")
        if frozen_request_digest and not _DIGEST.fullmatch(frozen_request_digest):
            return "remote operation request_digest is invalid"
        if not str(operation.get("remote_operation_id") or "").strip():
            return "remote operation id is missing"
        return ""

    def _validate_output(self, output: Any) -> None:
        try:
            Draft202012Validator(self.spec.output_schema).validate(output)
        except ValidationError as exc:
            raise ToolInvocationError(
                "REMOTE_OUTPUT_INVALID", "remote capability output violates its published schema"
            ) from exc

    @staticmethod
    def _error(value: dict[str, Any]) -> dict[str, Any]:
        error = value.get("error") or {}
        if not isinstance(error, dict):
            error = {}
        return {
            "code": str(error.get("code") or "REMOTE_CAPABILITY_FAILED")[:128],
            "message": sanitize_error_message(
                str(error.get("message") or value.get("summary") or "remote capability failed")
            ),
            "retryable": bool(error.get("retryable", False)),
        }


async def connect_remote_capabilities(
    services: dict[str, Any], registry: Any, lifecycle: Any
) -> None:
    """Register configured remote capabilities without loading business code."""
    seen: set[tuple[str, str]] = set()
    for raw_service_id, raw in sorted(services.items()):
        service_id = str(raw_service_id).strip()
        raw_value = connector_settings(raw)
        if not bool(raw_value.get("enabled", True)):
            continue
        service = _service_config(service_id, raw_value)
        client = await lifecycle.enter_async_context(
            httpx.AsyncClient(
                timeout=httpx.Timeout(service.timeout_seconds),
                follow_redirects=False,
            )
        )
        for spec in service.capabilities:
            identity = (spec.capability_id, spec.version)
            if identity in seen:
                raise ValueError(
                    f"remote capability {spec.capability_id}@{spec.version} is configured twice"
                )
            seen.add(identity)
            tool = RemoteCapabilityTool(service, spec, client)
            registry.register_tool(
                tool,
                optional=False,
                definition=_definition(service, spec),
            )


async def _connect(request: ToolConnectorConnectRequest) -> None:
    settings = connector_settings(request.settings)
    services = settings.get("services")
    if services in (None, {}):
        return
    if not isinstance(services, dict):
        raise TypeError("connector-http-capability settings.services must be an object")
    await connect_remote_capabilities(services, request.registry, request.lifecycle)


async def _preflight(settings: dict[str, Any]) -> dict[str, Any]:
    service_id = str(settings.get("service_id") or "").strip()
    service = settings.get("service")
    if not service_id or not isinstance(service, dict):
        raise TypeError("HTTP Capability Connector preflight requires service_id and service")
    return await preflight_extension_host(service_id, service)


HTTP_CAPABILITY_CONNECTOR = ToolConnectorExtension(
    manifest=HTTP_CAPABILITY_CONNECTOR_MANIFEST,
    connect=_connect,
    preflight=_preflight,
)


def create_extension() -> ToolConnectorExtension:
    return HTTP_CAPABILITY_CONNECTOR
