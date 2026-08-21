from __future__ import annotations

import pytest
from joyhousebot_sdk import AppClient, AppSimulator, verify_callback

from joyhousebot.domain.app_callbacks import callback_body, callback_signature


@pytest.mark.asyncio
async def test_app_sdk_exchanges_launches_and_waits_with_simulator() -> None:
    simulator = AppSimulator()
    async with AppClient(
        "https://runtime.test",
        client_id=simulator.client_id,
        client_secret=simulator.client_secret,
        installation_id=simulator.installation_id,
        transport=simulator.transport(),
    ) as client:
        entrypoint = await client.resolve_entrypoint("default", app_id=simulator.app_id)
        assert entrypoint["id"] == simulator.entrypoint_id
        launched = await client.run_entrypoint(
            "default",
            {"goal": "research the market"},
            idempotency_key="app-order-42",
            app_id=simulator.app_id,
        )
        repeated = await client.run_entrypoint(
            "default",
            {"goal": "research the market"},
            idempotency_key="app-order-42",
            app_id=simulator.app_id,
        )
        assert repeated.id == launched.id
        completed = await launched.wait(poll_seconds=0.05)
        assert completed.status == "succeeded"


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
    verified = verify_callback(
        {
            "X-JoyHouseBot-Timestamp": timestamp,
            "X-JoyHouseBot-Signature": callback_signature(
                secret, timestamp=timestamp, body=body
            ),
            "X-JoyHouseBot-Event-ID": "event-2",
            "X-JoyHouseBot-Event-Type": "run.completed",
        },
        body,
        secret=secret,
        now=1000,
    )
    assert verified.replay_of_event_id == "event-1"
    assert verified.replay_sequence == 1
    with pytest.raises(ValueError, match="signature"):
        verify_callback(
            {
                "X-JoyHouseBot-Timestamp": timestamp,
                "X-JoyHouseBot-Signature": "v1=invalid",
                "X-JoyHouseBot-Event-ID": "event-2",
                "X-JoyHouseBot-Event-Type": "run.completed",
            },
            body,
            secret=secret,
            now=1000,
        )
