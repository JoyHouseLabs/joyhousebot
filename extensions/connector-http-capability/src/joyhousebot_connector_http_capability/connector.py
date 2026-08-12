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
from jsonschema import Draft202012Validator, ValidationError

from joyhousebot.extension_sdk import (
    Artifact,
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
    ExtensionManifest,
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


@dataclass(frozen=True, slots=True)
class RemoteServiceConfig:
    service_id: str
    base_url: str
    key_id: str
    signing_secret: str
    timeout_seconds: float
    max_response_bytes: int
    require_response_signature: bool
    capabilities: tuple[RemoteCapabilitySpec, ...]


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
    )


def _service_config(service_id: str, raw: Any) -> RemoteServiceConfig:
    if not _SERVICE_ID.fullmatch(service_id):
        raise ValueError(f"remote capability service id {service_id!r} is invalid")
    value = connector_settings(raw)
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
    return RemoteServiceConfig(
        service_id=service_id,
        base_url=base_url,
        key_id=key_id,
        signing_secret=secret,
        timeout_seconds=timeout,
        max_response_bytes=max_bytes,
        require_response_signature=bool(value.get("require_response_signature", True)),
        capabilities=capabilities,
    )


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
        expected_duration_seconds=spec.expected_duration_seconds,
        timeout_seconds=spec.timeout_seconds,
        idempotent=spec.idempotent,
        retryable=spec.retryable,
        side_effect=spec.side_effect,
        invocation_concurrency=spec.invocation_concurrency,
        max_concurrent_invocations=spec.max_concurrent_invocations,
        permissions=spec.permissions,
        data_classification=spec.data_classification,
        connection_ids=(service.service_id,),
        cost_policy=spec.cost_policy,
        origin={
            "extension_id": HTTP_CAPABILITY_CONNECTOR_MANIFEST.extension_id,
            "extension_version": HTTP_CAPABILITY_CONNECTOR_MANIFEST.version,
            "extension_build_digest": HTTP_CAPABILITY_CONNECTOR_MANIFEST.build_digest,
            "remote_service_id": service.service_id,
            "remote_implementation_digest": spec.implementation_digest,
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
        payload = {
            "protocol_version": _PROTOCOL_VERSION,
            "capability": self._capability_identity(),
            "subject": {
                "user_id": context.user_id,
                "agent_id": context.agent_id,
                "session_id": context.session_id or context.session_key,
            },
            "execution": self._execution_identity(context),
            "authorization": {
                "permissions": sorted(context.granted_permissions),
                "permission_mode": context.permission_mode,
            },
            "input": kwargs,
        }
        path = f"/capabilities/{quote(self.spec.capability_id, safe='')}:invoke"
        response = await self._request(path, payload, context=context)
        return self._tool_output(response, context)

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
            "execution": self._execution_identity(context),
            "operation": {"operation_id": str(operation["remote_operation_id"])},
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
        )
        if status == "pending":
            retry_after = value.get("retry_after_seconds")
            return OperationReconciliationResult(
                status="pending",
                summary=str(value.get("summary") or "remote operation is pending"),
                operation=next_operation,
                retry_after_seconds=(int(retry_after) if retry_after is not None else None),
            )
        if status == "unknown":
            return OperationReconciliationResult(
                status="unknown",
                summary=str(value.get("summary") or "remote operation outcome is unknown"),
                operation=next_operation,
            )
        if status == "failed":
            return OperationReconciliationResult(
                status="failed",
                summary=str(value.get("summary") or "remote operation failed"),
                error=self._error(value),
                operation=next_operation,
            )
        output = value.get("output")
        self._validate_output(output)
        return OperationReconciliationResult(
            status="succeeded",
            summary=str(value.get("summary") or "remote operation completed"),
            output=output,
            artifacts=[_artifact(item) for item in value.get("artifacts") or []],
            operation=next_operation,
        )

    def _capability_identity(self) -> dict[str, str]:
        return {
            "capability_id": self.spec.capability_id,
            "version": self.spec.version,
            "implementation_digest": self.spec.implementation_digest,
        }

    @staticmethod
    def _execution_identity(context: Any) -> dict[str, Any]:
        return {
            "run_id": context.run_id,
            "root_run_id": context.root_run_id or context.run_id,
            "task_id": context.task_id,
            "request_id": context.request_id,
            "action_id": context.action_id,
            "idempotency_key": context.idempotency_key,
        }

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

    def _tool_output(self, value: dict[str, Any], context: Any) -> ToolOutput:
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
            operation = self._operation_descriptor(context, operation_id=operation_id or None)
        summary = str(value.get("summary") or "remote capability completed")
        data = {"output": output}
        if isinstance(value.get("usage"), dict):
            data["remote_usage"] = dict(value["usage"])
        return ToolOutput(
            content=summary if output is None else json.dumps(output, ensure_ascii=False),
            summary=summary,
            data=data,
            artifacts=tuple(_artifact(item).to_dict() for item in value.get("artifacts") or []),
            operation=operation,
            status=(
                InvocationStatus.ACCEPTED
                if status == "accepted"
                else InvocationStatus.SUCCEEDED
            ),
        )

    def _operation_descriptor(
        self, context: Any, *, operation_id: str | None
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


HTTP_CAPABILITY_CONNECTOR = ToolConnectorExtension(
    manifest=HTTP_CAPABILITY_CONNECTOR_MANIFEST,
    connect=_connect,
)


def create_extension() -> ToolConnectorExtension:
    return HTTP_CAPABILITY_CONNECTOR
