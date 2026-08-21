from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.capabilities.dispatcher import CapabilityDispatcher
from joyhousebot.config.schema import Config
from joyhousebot.model_gateway.app import create_model_gateway_app
from joyhousebot.model_gateway.service import HostModelGatewayService
from joyhousebot.providers.base import LLMResponse
from joyhousebot.providers.usage import normalized_usage
from joyhousebot.runtime.context import ActionOutcomeUnknownError
from joyhousebot.storage.json_codec import Jsonb
from tests.support.postgres_store import PostgresTestStore
from tests.test_operation_reconciliation import (
    _adapter,
    _AsyncOperationTool,
    _claimed_context,
)


class _FakeProvider:
    calls = 0

    async def chat(self, **_: object) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content="a governed answer",
            usage=normalized_usage(
                input_tokens=12,
                output_tokens=4,
                provider_cost_usd=0.000012,
            ),
        )

    async def close(self) -> None:
        return None


def _seed_provider(store: PostgresTestStore) -> tuple[str, str, str]:
    provider_id = "testhost"
    revision_id = "testhost:v1"
    model_id = "testhost/chat-v1"
    configuration = {
        "enabled": True,
        "extension_id": "provider-testhost",
        "api_base": "http://127.0.0.1:9999/v1",
        "credential_mode": "none",
        "api_key_ref": "",
        "extra_header_refs": {},
        "request_timeout_seconds": 30,
        "models": [
            {
                "model_id": model_id,
                "name": "Host Test Model",
                "kind": "llm",
                "enabled": True,
                "input_modalities": ["text"],
                "context_window": 100_000,
                "max_output_tokens": 4_096,
                "supports_tools": True,
                "supports_reasoning": False,
                "supports_structured_output": False,
                "default_temperature": 0.3,
                "tags": [],
                "dimensions": None,
                "input_cost_per_million_tokens": 1.0,
                "output_cost_per_million_tokens": 2.0,
                "cached_input_cost_per_million_tokens": None,
                "cache_creation_input_cost_per_million_tokens": None,
            }
        ],
    }
    with store._pool.connection() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO model_providers
                   (provider_id,name,description,current_revision_id)
               VALUES (%s,%s,'',%s)""",
            (provider_id, "Host Test", revision_id),
        )
        conn.execute(
            """INSERT INTO model_provider_revisions
                   (provider_id,revision_id,version,status,configuration,
                    fingerprint,created_by,published_at)
               VALUES (%s,%s,1,'published',%s,%s,'test',clock_timestamp())""",
            (provider_id, revision_id, Jsonb(configuration), f"sha256:{'c' * 64}"),
        )
    return provider_id, revision_id, model_id


@pytest.mark.asyncio
async def test_host_model_gateway_pins_scope_budgets_and_replays_response(
    tmp_path: Path,
) -> None:
    store = PostgresTestStore(tmp_path / "host-model-gateway.db")
    provider_id, revision_id, model_id = _seed_provider(store)
    _, execution = _claimed_context(store, "run-host-model-gateway")
    with pytest.raises(ActionOutcomeUnknownError) as raised:
        await CapabilityDispatcher(store).invoke_tool(
            _adapter(_AsyncOperationTool()),
            {"value": "one"},
            context=execution,
        )
    reconciliation = store.get_action_reconciliation(raised.value.action_id)
    assert reconciliation is not None
    store.create_api_access_token(
        user_id="user-a",
        actor_id="test",
        token="model-grant-owner-token",
        scopes=["devices.*", "runs.*"],
    )
    runtime_client = TestClient(
        create_app(build_api_container(config=Config(), store=store))
    )
    owner_headers = {"Authorization": "Bearer model-grant-owner-token"}

    with runtime_client:
        registration = runtime_client.post(
            "/host/v1/device-hosts",
            headers=owner_headers,
            json={
                "device_id": "host-model-device",
                "display_name": "Model Host",
                "host_revision": "device-host@1",
                "host_manifest_digest": f"sha256:{'a' * 64}",
                "capabilities": [
                    {
                        "capability_id": reconciliation.capability_ref["capability_id"],
                        "version": reconciliation.capability_ref["version"],
                        "implementation_digest": f"sha256:{'b' * 64}",
                    }
                ],
            },
        )
        assert registration.status_code == 201, registration.text
        device_headers = {
            "Authorization": f"Bearer {registration.json()['device_token']}",
            "X-JoyHouseBot-Device-ID": "host-model-device",
        }
        delivery_response = runtime_client.post(
            f"/host/v1/runs/{execution.run_id}/operations/"
            f"{reconciliation.reconciliation_id}/device-deliveries",
            headers=owner_headers,
            json={
                "device_id": "host-model-device",
                "operation_id": "provider-42",
                "model_access": {
                    "provider_id": provider_id,
                    "provider_revision_id": revision_id,
                    "model_id": model_id,
                    "token_budget": 10_000,
                    "cost_budget_micros": 10_000,
                    "max_concurrent": 1,
                    "expires_in_seconds": 300,
                },
            },
        )
        assert delivery_response.status_code == 202, delivery_response.text
        delivery_id = delivery_response.json()["delivery"]["delivery_id"]
        claim = runtime_client.post(
            "/host/v1/device-host/operations:claim",
            headers=device_headers,
            json={"claim_session_id": "device-model-session-0001", "lease_seconds": 300},
        )
        assert claim.status_code == 200, claim.text
        grant_response = runtime_client.post(
            f"/host/v1/device-host/operations/{delivery_id}/model-grant",
            headers=device_headers,
            json={
                "claim_session_id": "device-model-session-0001",
                "claim_version": claim.json()["items"][0]["claim_version"],
            },
        )
        assert grant_response.status_code == 200, grant_response.text
        grant_token = grant_response.json()["model_grant_token"]
        grant_id = grant_response.json()["grant"]["grant_id"]
        assert grant_token.startswith("jhm_")
        listed = runtime_client.get("/host/v1/model-grants", headers=owner_headers)
        assert listed.status_code == 200
        assert "fingerprint" not in str(listed.json()).lower()

    held, created = store.reserve_host_model_budget(
        grant_id,
        reservation_id="model-reservation-held",
        request_id="host-model-concurrency-held",
        reserved_tokens=100,
        reserved_cost_micros=100,
        reservation_seconds=300,
    )
    assert held is not None and created
    blocked, blocked_created = store.reserve_host_model_budget(
        grant_id,
        reservation_id="model-reservation-blocked",
        request_id="host-model-concurrency-blocked",
        reserved_tokens=100,
        reserved_cost_micros=100,
        reservation_seconds=300,
    )
    assert blocked is None and not blocked_created
    assert store.release_host_model_budget(held.reservation_id)

    provider = _FakeProvider()
    gateway_service = HostModelGatewayService(
        store=store,
        config=Config(),
        provider_factory=lambda **_: provider,
    )
    gateway_client = TestClient(
        create_model_gateway_app(store=store, config=Config(), service=gateway_service)
    )
    request = {
        "request_id": "host-model-request-0001",
        "model": model_id,
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 64,
    }
    with gateway_client:
        first = gateway_client.post(
            "/v1/chat",
            headers={"Authorization": f"Bearer {grant_token}"},
            json=request,
        )
        assert first.status_code == 200, first.text
        replay = gateway_client.post(
            "/v1/chat",
            headers={"Authorization": f"Bearer {grant_token}"},
            json=request,
        )
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()
        assert provider.calls == 1
        wrong_model = gateway_client.post(
            "/v1/chat",
            headers={"Authorization": f"Bearer {grant_token}"},
            json={**request, "request_id": "host-model-request-0002", "model": "other/model"},
        )
        assert wrong_model.status_code == 403
        compatible = gateway_client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {grant_token}",
                "Idempotency-Key": "host-model-openai-0001",
            },
            json={
                "model": model_id,
                "messages": [{"role": "user", "content": "hello again"}],
                "max_tokens": 64,
            },
        )
        assert compatible.status_code == 200, compatible.text
        assert compatible.json()["object"] == "chat.completion"
        assert compatible.json()["choices"][0]["message"]["content"] == "a governed answer"

    grants = store.list_host_model_grants(user_id="user-a")
    assert len(grants) == 1
    assert grants[0].used_tokens == 32
    assert grants[0].used_cost_micros == 24
    invocations = store.list_model_invocations(execution.run_id)
    assert len(invocations) == 2
    assert all(item.operation == "host_gateway.chat" for item in invocations)
