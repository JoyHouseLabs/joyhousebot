"""Expose separately deployed business applications as governed capabilities."""

from __future__ import annotations

import hmac
import json
import time
from typing import Any, Callable
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
from jsonschema import Draft202012Validator, ValidationError

from porthouse.extension_sdk import (
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
from porthouse.extension_sdk.connectors import connector_settings
from porthouse.extension_sdk.manifest import source_tree_digest
from porthouse.extension_sdk.network import sanitize_error_message
from porthouse.extension_sdk.tools import (
    InvocationStatus,
    Tool,
    ToolInvocationError,
    ToolOutput,
)
from porthouse_connector_http_capability.config import (
    _DIGEST,
    _MAX_RESPONSE_BYTES,
    _PROTOCOL_VERSION,
    RemoteCapabilitySpec,
    RemoteServiceConfig,
    _canonical_json,
    _service_config,
    preflight_extension_host,
    request_digest,
    sign_request_body,
    sign_response_body,
)
from porthouse_connector_http_capability.config import (
    extension_host_manifest_digest as extension_host_manifest_digest,
)

_BUILD_DIGEST = source_tree_digest(__file__)

HTTP_CAPABILITY_CONNECTOR_MANIFEST = ExtensionManifest(
    extension_id="connector-http-capability",
    version="0.1.1",
    name="Porthouse HTTP Capability Connector",
    extension_types=("tool_connector",),
    description=(
        "Invoke versioned capabilities implemented by separately deployed business applications."
    ),
    distribution_name="porthouse-connector-http-capability",
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
            "X-Porthouse-Capability-Protocol": _PROTOCOL_VERSION,
            "X-Porthouse-Key-Id": self.service.key_id,
            "X-Porthouse-Timestamp": timestamp,
            "X-Porthouse-Nonce": nonce,
            "X-Porthouse-Signature": signature,
            "X-Porthouse-Run-ID": str(context.run_id),
            "Idempotency-Key": str(context.idempotency_key or ""),
        }
        if context.action_id:
            headers["X-Porthouse-Action-ID"] = str(context.action_id)
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
            received = str(response.headers.get("X-Porthouse-Response-Signature") or "")
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
