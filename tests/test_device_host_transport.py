from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from porthouse.api.app import create_app
from porthouse.bootstrap.container import build_api_container
from porthouse.bootstrap.host_tool_worker import HostToolBrokerWorker
from porthouse.capabilities import CapabilityRegistry
from porthouse.capabilities.dispatcher import CapabilityDispatcher
from porthouse.capabilities.tool_adapter import ToolOutput
from porthouse.config.schema import Config
from porthouse.contracts.tools import Tool
from porthouse.runtime.context import ActionOutcomeUnknownError
from tests.support.capabilities import register_tool_fixture
from tests.support.postgres_store import PostgresTestStore
from tests.test_operation_reconciliation import (
    _adapter,
    _AsyncOperationTool,
    _claimed_context,
)


class _HostReadTool(Tool):
    name = "host_read"
    description = "Return a value through the governed Host Tool Broker"
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    side_effect = "read"

    async def execute(self, value: str, **_kwargs: Any) -> ToolOutput:
        return ToolOutput(
            content=f"read:{value}",
            summary="host read completed",
            data={"value": value},
        )


@pytest.mark.asyncio
async def test_device_host_claim_is_fenced_and_completion_resumes_operation(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "device-host.db")
    _, execution = _claimed_context(store, "run-device-host")
    adapter = _adapter(_AsyncOperationTool())
    with pytest.raises(ActionOutcomeUnknownError) as raised:
        await CapabilityDispatcher(store).invoke_tool(
            adapter, {"value": "one"}, context=execution
        )
    reconciliation = store.get_action_reconciliation(raised.value.action_id)
    assert reconciliation is not None
    store.create_run_execution_snapshot(execution.run_id, "default")
    registry = CapabilityRegistry(store=store)
    host_tool_definition = register_tool_fixture(registry, _HostReadTool())

    store.create_api_access_token(
        user_id="user-a", actor_id="test", token="device-owner-token"
    )
    store.create_api_access_token(
        user_id="user-b", actor_id="test", token="foreign-owner-token"
    )
    client = TestClient(
        create_app(build_api_container(config=Config(), store=store))
    )
    headers = {"Authorization": "Bearer device-owner-token"}
    capability = reconciliation.capability_ref
    registration_body = {
        "device_id": "macbook-a",
        "display_name": "My MacBook",
        "host_revision": "node-host@1.0.0+build-a",
        "host_manifest_digest": f"sha256:{'a' * 64}",
        "capabilities": [
            {
                "capability_id": capability["capability_id"],
                "version": capability["version"],
                "implementation_digest": f"sha256:{'b' * 64}",
                "portable": False,
            }
        ],
    }

    with client:
        registered = client.post(
            "/v1/device-hosts", headers=headers, json=registration_body
        )
        assert registered.status_code == 201, registered.text
        device_token = registered.json()["device_token"]
        assert device_token.startswith("jhd_")
        assert "token" not in str(
            client.get("/v1/device-hosts", headers=headers).json()["items"][0]
        ).lower()

        foreign = client.post(
            f"/v1/runs/{execution.run_id}/operations/"
            f"{reconciliation.reconciliation_id}/device-deliveries",
            headers={"Authorization": "Bearer foreign-owner-token"},
            json={"device_id": "macbook-a", "operation_id": "provider-42"},
        )
        assert foreign.status_code == 404

        recursive = client.post(
            f"/v1/runs/{execution.run_id}/operations/"
            f"{reconciliation.reconciliation_id}/device-deliveries",
            headers=headers,
            json={
                "device_id": "macbook-a",
                "operation_id": "recursive-provider",
                "tool_access": [
                    {
                        "capability_id": capability["capability_id"],
                        "version": capability["version"],
                    }
                ],
            },
        )
        assert recursive.status_code == 422

        created = client.post(
            f"/v1/runs/{execution.run_id}/operations/"
            f"{reconciliation.reconciliation_id}/device-deliveries",
            headers=headers,
            json={
                "device_id": "macbook-a",
                "operation_id": "provider-42",
                "tool_access": [
                    {
                        "capability_id": host_tool_definition.ref.capability_id,
                        "version": host_tool_definition.ref.version,
                    }
                ],
            },
        )
        assert created.status_code == 202, created.text
        delivery_id = created.json()["delivery"]["delivery_id"]

        device_headers = {
            "Authorization": f"Bearer {device_token}",
            "X-Porthouse-Device-ID": "macbook-a",
        }
        heartbeat = client.post(
            "/v1/device-host/heartbeat",
            headers=device_headers,
            json={
                "host_revision": registration_body["host_revision"],
                "host_manifest_digest": registration_body["host_manifest_digest"],
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text

        session_id = "device-session-0001"
        claimed = client.post(
            "/v1/device-host/operations:claim",
            headers=device_headers,
            json={"claim_session_id": session_id, "lease_seconds": 30},
        )
        assert claimed.status_code == 200, claimed.text
        delivery = claimed.json()["items"][0]
        assert delivery["request"]["input"] == {"value": "one"}
        assert delivery["request"]["execution"]["action_id"] == reconciliation.action_id
        claim_version = delivery["claim_version"]

        issued_tool_grant = client.post(
            f"/v1/device-host/operations/{delivery_id}/tool-grant",
            headers=device_headers,
            json={
                "claim_session_id": session_id,
                "claim_version": claim_version,
                "expires_in_seconds": 120,
            },
        )
        assert issued_tool_grant.status_code == 200, issued_tool_grant.text
        tool_grant_token = issued_tool_grant.json()["tool_grant_token"]
        assert tool_grant_token.startswith("jht_")
        tool_headers = {"Authorization": f"Bearer {tool_grant_token}"}

        host_request_body = {
            "host_request_id": "pi-tool-call-1",
            "capability_id": host_tool_definition.ref.capability_id,
            "capability_version": host_tool_definition.ref.version,
            "input": {"value": "from-node"},
        }
        submitted_tool = client.post(
            "/v1/host-tool-requests",
            headers=tool_headers,
            json=host_request_body,
        )
        assert submitted_tool.status_code == 202, submitted_tool.text
        assert submitted_tool.json()["created"] is True
        host_tool_request_id = submitted_tool.json()["request"]["request_id"]
        duplicate_tool = client.post(
            "/v1/host-tool-requests",
            headers=tool_headers,
            json=host_request_body,
        )
        assert duplicate_tool.status_code == 202, duplicate_tool.text
        assert duplicate_tool.json()["created"] is False
        denied_tool = client.post(
            "/v1/host-tool-requests",
            headers=tool_headers,
            json={
                **host_request_body,
                "host_request_id": "pi-tool-call-denied",
                "capability_id": "unfrozen.tool",
            },
        )
        assert denied_tool.status_code == 422

        claimed_tool = store.claim_host_tool_request(
            worker_id="host-tool-worker-test", lease_seconds=30
        )
        assert claimed_tool is not None
        host_tool_worker = HostToolBrokerWorker(
            store=store,
            catalog=SimpleNamespace(
                resolve=lambda _revision_id: SimpleNamespace(capabilities=registry)
            ),
            worker_id="host-tool-worker-test",
            lease_seconds=30,
        )
        await host_tool_worker.execute(claimed_tool)
        polled_tool = client.get(
            f"/v1/host-tool-requests/{host_tool_request_id}",
            headers=tool_headers,
        )
        assert polled_tool.status_code == 200, polled_tool.text
        assert polled_tool.json()["request"]["status"] == "succeeded"
        assert polled_tool.json()["request"]["result"]["data"] == {
            "value": "from-node"
        }
        for index in range(2, 65):
            within_budget = client.post(
                "/v1/host-tool-requests",
                headers=tool_headers,
                json={
                    **host_request_body,
                    "host_request_id": f"pi-tool-call-{index}",
                },
            )
            assert within_budget.status_code == 202, within_budget.text
        exhausted = client.post(
            "/v1/host-tool-requests",
            headers=tool_headers,
            json={
                **host_request_body,
                "host_request_id": "pi-tool-call-over-budget",
            },
        )
        assert exhausted.status_code == 409

        stale = client.post(
            f"/v1/device-host/operations/{delivery_id}/events:append",
            headers=device_headers,
            json={
                "claim_session_id": session_id,
                "claim_version": claim_version + 1,
                "events": [
                    {
                        "event_id": "event-stale",
                        "sequence": 0,
                        "event_type": "progress",
                        "summary": "stale",
                    }
                ],
            },
        )
        assert stale.status_code == 409

        appended = client.post(
            f"/v1/device-host/operations/{delivery_id}/events:append",
            headers=device_headers,
            json={
                "claim_session_id": session_id,
                "claim_version": claim_version,
                "events": [
                    {
                        "event_id": "event-1",
                        "sequence": 0,
                        "event_type": "progress",
                        "summary": "Browser operation finished",
                    }
                ],
            },
        )
        assert appended.status_code == 200, appended.text

        result = {
            "invocation_id": reconciliation.invocation_id,
            "status": "succeeded",
            "summary": "Device completed the operation",
            "data": {"value": "done-on-device"},
            "artifacts": [],
        }
        completed = client.post(
            f"/v1/device-host/operations/{delivery_id}:complete",
            headers=device_headers,
            json={
                "claim_session_id": session_id,
                "claim_version": claim_version,
                "result": result,
            },
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["delivery"]["status"] == "completed"

        replay = client.post(
            f"/v1/device-host/operations/{delivery_id}:complete",
            headers=device_headers,
            json={
                "claim_session_id": session_id,
                "claim_version": claim_version,
                "result": result,
            },
        )
        assert replay.status_code == 200, replay.text

        revoked = client.delete("/v1/device-hosts/macbook-a", headers=headers)
        assert revoked.status_code == 204
        rejected = client.post(
            "/v1/device-host/operations:claim",
            headers=device_headers,
            json={"claim_session_id": "device-session-0002"},
        )
        assert rejected.status_code == 401
        rejected_tool_grant = client.get(
            f"/v1/host-tool-requests/{host_tool_request_id}",
            headers=tool_headers,
        )
        assert rejected_tool_grant.status_code == 401

    saved = store.get_action_reconciliation(reconciliation.action_id)
    assert saved is not None and saved.status == "succeeded"
    assert saved.result is not None
    assert saved.result["data"] == {"value": "done-on-device"}
    events = store.list_device_operation_events(
        delivery_id, expected_user_id="user-a"
    )
    assert [event.event_id for event in events] == ["event-1"]
