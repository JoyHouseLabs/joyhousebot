from __future__ import annotations

import json
from contextlib import AsyncExitStack
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from porthouse_connector_http_capability import (
    HTTP_CAPABILITY_CONNECTOR_MANIFEST,
    RemoteCapabilityTool,
    connect_remote_capabilities,
    connector,
    create_extension,
    sign_request_body,
    sign_response_body,
)

from porthouse.capabilities import CapabilityRegistry
from porthouse.config.loader import load_config
from porthouse.connectors import ToolConnectorRegistry
from porthouse.extension_sdk.tools import InvocationStatus, ToolInvocationError

SECRET = "test-signing-secret-that-is-at-least-32-bytes"
DIGEST = f"sha256:{'1' * 64}"


def _capability(**overrides: Any) -> dict[str, Any]:
    value = {
        "capability_id": "crm.lead.read",
        "version": "1.0.0",
        "implementation_digest": DIGEST,
        "name": "Read lead",
        "description": "Read one lead",
        "input_schema": {
            "type": "object",
            "properties": {"lead_id": {"type": "string"}},
            "required": ["lead_id"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "required": ["lead_id"],
            "properties": {"lead_id": {"type": "string"}},
        },
        "permissions": ["crm.lead.read"],
        "side_effect": "read",
        "idempotent": True,
        "data_classification": "confidential",
    }
    value.update(overrides)
    return value


def _service(*, capabilities: list[dict[str, Any]] | None = None, **overrides: Any):
    value = {
        "service_profile": "business",
        "base_url": "https://crm.example.test/porthouse/v1",
        "key_id": "test-key",
        "signing_secret": SECRET,
        "capabilities": capabilities or [_capability()],
    }
    value.update(overrides)
    return connector._service_config("crm", value)


def _context(*, write: bool = False):
    return SimpleNamespace(
        user_id="user-1",
        agent_id="agent-1",
        session_id="session-1",
        session_key="api:user-1:agent-1:session-1",
        run_id="run-1",
        root_run_id="run-1",
        task_id="task-1",
        request_id="request-1",
        action_id="act-1" if write else None,
        idempotency_key="action:act-1" if write else "tool:read-1",
        granted_permissions=frozenset({"crm.lead.read", "crm.lead.update"}),
        permission_mode="default",
    )


def _signed_response(request: httpx.Request, payload: dict[str, Any], status: int = 200):
    body = connector._canonical_json(payload)
    signature = sign_response_body(
        status_code=status,
        nonce=request.headers["X-Porthouse-Nonce"],
        body=body,
        secret=SECRET,
    )
    return httpx.Response(
        status,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Porthouse-Response-Signature": signature,
        },
    )


def test_connector_has_one_independent_tool_connector_manifest():
    extension = create_extension()
    assert extension.manifest is HTTP_CAPABILITY_CONNECTOR_MANIFEST
    assert extension.manifest.extension_id == "connector-http-capability"
    assert extension.manifest.extension_types == ("tool_connector",)
    assert extension.manifest.build_digest.startswith("sha256:")


def test_remote_definition_is_pinned_without_owning_business_code():
    service = _service()
    definition = connector._definition(service, service.capabilities[0])
    assert definition.ref.kind.value == "connector"
    assert definition.ref.plugin_id == "connector-http-capability"
    assert definition.origin["remote_service_id"] == "crm"
    assert definition.origin["remote_implementation_digest"] == DIGEST
    assert definition.permissions == ("crm.lead.read",)
    assert definition.connection_ids == ("crm",)


def test_remote_write_must_be_idempotent_and_plain_http_is_loopback_only():
    with pytest.raises(ValueError, match="must honor"):
        _service(
            capabilities=[
                _capability(
                    capability_id="crm.lead.update",
                    side_effect="update",
                    idempotent=False,
                )
            ]
        )
    with pytest.raises(ValueError, match="requires HTTPS"):
        _service(base_url="http://crm.internal/porthouse/v1", allow_insecure_http=True)
    loopback = _service(
        base_url="http://127.0.0.1:9000/porthouse/v1", allow_insecure_http=True
    )
    assert loopback.base_url.startswith("http://127.0.0.1:9000/")


def test_connector_secret_must_come_from_environment(tmp_path, monkeypatch):
    plaintext = tmp_path / "plaintext.json"
    plaintext.write_text(
        '{"extensions":{"settings":{"connector-http-capability":{"services":'
        '{"crm":{"signingSecret":"plaintext"}}}}}}'
    )
    with pytest.raises(ValueError, match="plaintext secret"):
        load_config(plaintext)

    monkeypatch.setenv("REMOTE_CAPABILITY_TEST_SECRET", SECRET)
    referenced = tmp_path / "referenced.json"
    referenced.write_text(
        '{"extensions":{"settings":{"connector-http-capability":{"services":'
        '{"crm":{"signingSecret":"env://REMOTE_CAPABILITY_TEST_SECRET"}}}}}}'
    )
    settings = load_config(referenced).extensions.settings["connector-http-capability"]
    assert settings["services"]["crm"]["signing_secret"] == SECRET


@pytest.mark.asyncio
async def test_signed_read_invocation_uses_fixed_endpoint_and_frozen_identity():
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        expected = sign_request_body(
            method="POST",
            path=request.url.path,
            timestamp=request.headers["X-Porthouse-Timestamp"],
            nonce=request.headers["X-Porthouse-Nonce"],
            body=body,
            secret=SECRET,
        )
        assert request.headers["X-Porthouse-Signature"] == expected
        assert request.url.path == "/porthouse/v1/capabilities/crm.lead.read:invoke"
        payload = json.loads(body)
        assert payload["subject"]["user_id"] == "user-1"
        assert payload["execution"]["run_id"] == "run-1"
        assert payload["execution"]["request_digest"].startswith("sha256:")
        assert payload["capability"]["implementation_digest"] == DIGEST
        return _signed_response(
            request,
            {
                "protocol_version": "1",
                "status": "succeeded",
                "summary": "lead loaded",
                "output": {"lead_id": "lead-1"},
                "artifacts": [],
            },
        )

    service = _service()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = RemoteCapabilityTool(
            service,
            service.capabilities[0],
            client,
            clock=lambda: 1_700_000_000,
            nonce_factory=lambda: "nonce-1",
        )
        result = await tool.execute(lead_id="lead-1", tool_context=_context())
    assert result.status == InvocationStatus.SUCCEEDED
    assert result.data["output"] == {"lead_id": "lead-1"}


@pytest.mark.asyncio
async def test_write_receipt_must_match_runtime_action():
    def handler(request: httpx.Request) -> httpx.Response:
        return _signed_response(
            request,
            {
                "protocol_version": "1",
                "status": "succeeded",
                "output": {"lead_id": "lead-1"},
                "write_receipt": {
                    "action_id": "another-action",
                    "idempotency_key": "action:act-1",
                },
            },
        )

    service = _service(
        capabilities=[
            _capability(
                capability_id="crm.lead.update",
                permissions=["crm.lead.update"],
                side_effect="update",
                idempotent=True,
            )
        ]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = RemoteCapabilityTool(service, service.capabilities[0], client)
        with pytest.raises(ToolInvocationError) as raised:
            await tool.execute(lead_id="lead-1", tool_context=_context(write=True))
    assert raised.value.code == "WRITE_IDENTITY_MISMATCH"


@pytest.mark.asyncio
async def test_accepted_write_reconciles_without_resubmitting():
    calls: list[str] = []
    reconcile_requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith(":invoke"):
            return _signed_response(
                request,
                {
                    "protocol_version": "1",
                    "status": "accepted",
                    "summary": "queued",
                    "operation": {"operation_id": "crm-op-42"},
                    "write_receipt": {
                        "action_id": "act-1",
                        "idempotency_key": "action:act-1",
                    },
                },
                status=202,
            )
        reconcile_requests.append(json.loads(request.content))
        return _signed_response(
            request,
            {
                "protocol_version": "1",
                "status": "succeeded",
                "summary": "done",
                "operation": {"operation_id": "crm-op-42"},
                "output": {"lead_id": "lead-1"},
                "artifacts": [],
                "provider_cursor": "cursor-2",
                "checkpoint_ref": "checkpoint-2",
                "progress_summary": "complete",
                "progress_percent": 100,
                "events": [
                    {
                        "event_id": "event-2",
                        "sequence": 2,
                        "event_type": "operation.completed",
                        "summary": "complete",
                        "payload": {"safe": True},
                    }
                ],
            },
        )

    service = _service(
        capabilities=[
            _capability(
                capability_id="crm.lead.update",
                permissions=["crm.lead.update"],
                side_effect="update",
                idempotent=True,
            )
        ]
    )
    context = _context(write=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = RemoteCapabilityTool(service, service.capabilities[0], client)
        accepted = await tool.execute(lead_id="lead-1", tool_context=context)
        reconciled = await tool.reconcile_operation(
            {**(accepted.operation or {}), "provider_cursor": "cursor-1"},
            tool_context=context,
        )
    assert accepted.status == InvocationStatus.ACCEPTED
    assert accepted.operation["remote_operation_id"] == "crm-op-42"
    assert accepted.operation["request_digest"].startswith("sha256:")
    assert reconciled.status == "succeeded"
    assert reconciled.output == {"lead_id": "lead-1"}
    assert reconciled.provider_cursor == "cursor-2"
    assert reconciled.progress_percent == 100
    assert reconciled.events[0].event_id == "event-2"
    assert reconcile_requests[0]["operation"]["cursor"] == "cursor-1"
    assert calls == [
        "/porthouse/v1/capabilities/crm.lead.update:invoke",
        "/porthouse/v1/operations:reconcile",
    ]


@pytest.mark.asyncio
async def test_unsigned_response_fails_closed():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "protocol_version": "1",
                "status": "succeeded",
                "output": {"lead_id": "lead-1"},
            },
        )

    service = _service()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = RemoteCapabilityTool(service, service.capabilities[0], client)
        with pytest.raises(ToolInvocationError) as raised:
            await tool.execute(lead_id="lead-1", tool_context=_context())
    assert raised.value.code == "REMOTE_RESPONSE_SIGNATURE_INVALID"


@pytest.mark.asyncio
async def test_extension_host_uri_artifact_requires_runtime_upload_grant():
    def handler(request: httpx.Request) -> httpx.Response:
        return _signed_response(
            request,
            {
                "protocol_version": "1",
                "status": "succeeded",
                "output": {"lead_id": "lead-1"},
                "artifacts": [
                    {
                        "artifact_type": "host.output",
                        "media_type": "text/plain",
                        "uri": "file:///tmp/host-output.txt",
                    }
                ],
            },
        )

    service = _service(
        service_profile="extension_host",
        host_protocol_version="1",
        expected_host_manifest_digest=f"sha256:{'9' * 64}",
        require_host_preflight=True,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = RemoteCapabilityTool(service, service.capabilities[0], client)
        with pytest.raises(ToolInvocationError) as raised:
            await tool.execute(lead_id="lead-1", tool_context=_context())
    assert raised.value.code == "HOST_ARTIFACT_GRANT_REQUIRED"


@pytest.mark.asyncio
async def test_invalid_content_length_fails_closed():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"{}",
            headers={"Content-Length": "not-a-number"},
        )

    service = _service()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = RemoteCapabilityTool(service, service.capabilities[0], client)
        with pytest.raises(ToolInvocationError) as raised:
            await tool.execute(lead_id="lead-1", tool_context=_context())
    assert raised.value.code == "REMOTE_RESPONSE_INVALID"


@pytest.mark.asyncio
async def test_response_larger_than_connection_limit_fails_closed():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * 2048,
            headers={"Content-Length": "2048"},
        )

    service = _service(max_response_bytes=1024)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = RemoteCapabilityTool(service, service.capabilities[0], client)
        with pytest.raises(ToolInvocationError) as raised:
            await tool.execute(lead_id="lead-1", tool_context=_context())
    assert raised.value.code == "REMOTE_RESPONSE_TOO_LARGE"


@pytest.mark.asyncio
async def test_generic_registry_registers_declared_remote_capability():
    registry = CapabilityRegistry()
    async with AsyncExitStack() as stack:
        await connect_remote_capabilities(
            {"crm": connector_settings_for_test()}, registry, stack
        )
        definition = registry.get_definition("crm.lead.read", "1.0.0")
        assert definition is not None
        assert definition.adapter == "tool_connector:http-capability-v1"


@pytest.mark.asyncio
async def test_tool_connector_registry_connects_extension():
    called: list[dict[str, Any]] = []

    async def connect(request):
        called.append(request.settings)

    declared = create_extension()
    extension = type(declared)(manifest=declared.manifest, connect=connect)
    registry = ToolConnectorRegistry()
    registry.register(extension, source="test")
    async with AsyncExitStack() as stack:
        await registry.connect_configured(
            {"connector-http-capability": {"services": {"crm": {}}}},
            capability_registry=CapabilityRegistry(),
            lifecycle=stack,
        )
    assert called == [{"services": {"crm": {}}}]


def connector_settings_for_test() -> dict[str, Any]:
    return {
        "service_profile": "business",
        "base_url": "https://crm.example.test/porthouse/v1",
        "key_id": "test-key",
        "signing_secret": SECRET,
        "capabilities": [_capability()],
    }
