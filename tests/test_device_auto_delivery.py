"""Auto device delivery: frozen operations route to the paired phone.

Covers the Scheduler pass ``DeviceHostService.auto_enqueue_pending``: a
reconciliation whose exact capability an active device declared gets a
delivery automatically (idempotently), unmatched operations stay on the
manual path, and a device completion resolves the operation end to end.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.capabilities.dispatcher import CapabilityDispatcher
from joyhousebot.config.schema import Config
from joyhousebot.runtime.context import ActionOutcomeUnknownError
from tests.support.postgres_store import PostgresTestStore, require_postgres
from tests.test_operation_reconciliation import (
    _adapter,
    _claimed_context,
    _OpaqueAsyncTool,
)


class _UnmatchedAsyncTool(_OpaqueAsyncTool):
    name = "unmatched_operation"
    description = "An operation no device declares"


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_auto_delivery_routes_frozen_operations_to_the_paired_device(
    tmp_path: Path,
) -> None:
    require_postgres()
    store = PostgresTestStore(tmp_path / "device-auto-delivery.db")
    _, execution = _claimed_context(store, "run-auto-delivery")
    _, other_execution = _claimed_context(store, "run-auto-unmatched")

    with pytest.raises(ActionOutcomeUnknownError) as raised:
        await CapabilityDispatcher(store).invoke_tool(
            _adapter(_OpaqueAsyncTool()), {}, context=execution
        )
    reconciliation = store.get_action_reconciliation(raised.value.action_id)
    assert reconciliation is not None
    assert reconciliation.status == "manual_required"

    with pytest.raises(ActionOutcomeUnknownError):
        await CapabilityDispatcher(store).invoke_tool(
            _adapter(_UnmatchedAsyncTool()), {}, context=other_execution
        )

    store.create_api_access_token(
        user_id="user-a", actor_id="test", token="device-owner-token"
    )
    container = build_api_container(config=Config(), store=store)
    client = TestClient(create_app(container))
    headers = {"Authorization": "Bearer device-owner-token"}

    with client:
        registered = client.post(
            "/host/v1/device-hosts",
            headers=headers,
            json={
                "device_id": "pixel-a",
                "display_name": "Pixel",
                "host_revision": "android-host@1.0.0+build-a",
                "host_manifest_digest": f"sha256:{'a' * 64}",
                "capabilities": [
                    {
                        "capability_id": "opaque_operation",
                        "version": "1.0.0",
                        "implementation_digest": f"sha256:{'b' * 64}",
                        "portable": False,
                    }
                ],
            },
        )
        assert registered.status_code == 201, registered.text
        device_token = registered.json()["device_token"]

        candidates = store.find_device_delivery_candidates(
            limit=10, created_within_seconds=21_600
        )
        assert [item["reconciliation_id"] for item in candidates] == [
            reconciliation.reconciliation_id
        ]
        assert candidates[0]["device_id"] == "pixel-a"

        enqueued = await container.device_hosts.auto_enqueue_pending()
        assert enqueued == 1
        again = await container.device_hosts.auto_enqueue_pending()
        assert again == 0

        device_headers = {
            "Authorization": f"Bearer {device_token}",
            "X-JoyHouseBot-Device-ID": "pixel-a",
        }
        heartbeat = client.post(
            "/host/v1/device-host/heartbeat",
            headers=device_headers,
            json={
                "host_revision": "android-host@1.0.0+build-a",
                "host_manifest_digest": f"sha256:{'a' * 64}",
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text

        claimed = client.post(
            "/host/v1/device-host/operations:claim",
            headers=device_headers,
            json={"claim_session_id": "android-session-0001", "lease_seconds": 30},
        )
        assert claimed.status_code == 200, claimed.text
        items = claimed.json()["items"]
        assert len(items) == 1
        delivery = items[0]
        delivery_id = delivery["delivery_id"]
        assert delivery["request"]["capability"]["capability_id"] == "opaque_operation"
        assert delivery["request"]["execution"]["action_id"] == reconciliation.action_id
        claim_version = delivery["claim_version"]

        completed = client.post(
            f"/host/v1/device-host/operations/{delivery_id}:complete",
            headers=device_headers,
            json={
                "claim_session_id": "android-session-0001",
                "claim_version": claim_version,
                "result": {
                    "invocation_id": reconciliation.invocation_id,
                    "status": "succeeded",
                    "summary": "phone executed the op",
                    "data": {"value": "done-on-phone"},
                    "artifacts": [],
                },
            },
        )
        assert completed.status_code == 200, completed.text

    saved = store.get_action_reconciliation(reconciliation.action_id)
    assert saved is not None and saved.status == "succeeded"
    assert saved.result is not None
    assert saved.result["data"] == {"value": "done-on-phone"}
    assert saved.resolution_source == "device"
