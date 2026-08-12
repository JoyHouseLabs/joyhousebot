from __future__ import annotations

import pytest

from joyhousebot.app_sdk import (
    AppRuntimeClient,
    AppRuntimeSimulator,
    verify_app_callback,
)
from joyhousebot.domain.app_callbacks import callback_body, callback_signature


@pytest.mark.asyncio
async def test_app_sdk_exchanges_launches_and_waits_with_simulator() -> None:
    simulator = AppRuntimeSimulator()
    async with AppRuntimeClient(
        "https://runtime.test",
        client_id=simulator.client_id,
        client_secret=simulator.client_secret,
        grant_id=simulator.grant_id,
        transport=simulator.transport(),
    ) as client:
        apps = await client.list_apps()
        assert apps[0]["installation_id"] == simulator.installation_id
        launched = await client.launch(
            simulator.installation_id,
            "research the market",
            idempotency_key="app-order-42",
        )
        repeated = await client.launch(
            simulator.installation_id,
            "research the market",
            idempotency_key="app-order-42",
        )
        assert repeated["run_id"] == launched["run_id"]
        completed = await client.wait_run(launched["run_id"], poll_seconds=0.05)
        assert completed["status"] == "completed"


def test_app_sdk_verifies_callback_identity_signature_and_replay() -> None:
    secret = b"callback-secret-with-at-least-thirty-two-bytes"
    payload = {
        "schema_version": 1,
        "event_id": "event-2",
        "event_type": "run.completed",
        "run": {"run_id": "run-1", "status": "completed"},
        "delivery": {"replay_of_event_id": "event-1", "replay_sequence": 1},
    }
    body = callback_body(payload)
    timestamp = "1000"
    verified = verify_app_callback(
        {
            "X-Joyhouse-Timestamp": timestamp,
            "X-Joyhouse-Signature": callback_signature(
                secret, timestamp=timestamp, body=body
            ),
            "X-Joyhouse-Event-ID": "event-2",
            "X-Joyhouse-Event-Type": "run.completed",
        },
        body,
        secret=secret,
        now=1000,
    )
    assert verified.replay_of_event_id == "event-1"
    assert verified.replay_sequence == 1
    with pytest.raises(ValueError, match="signature"):
        verify_app_callback(
            {
                "X-Joyhouse-Timestamp": timestamp,
                "X-Joyhouse-Signature": "v1=invalid",
                "X-Joyhouse-Event-ID": "event-2",
                "X-Joyhouse-Event-Type": "run.completed",
            },
            body,
            secret=secret,
            now=1000,
        )
