from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
from joyhousebot_capability_media_generation import MediaGenerationExtension
from joyhousebot_capability_media_generation.jimeng import JimengAdapter
from joyhousebot_capability_media_generation.signing import sign_openapi_request

from joyhousebot.capabilities import CapabilityExtensionRegistry
from joyhousebot.extension_sdk import CapabilityContext


def _context() -> CapabilityContext:
    return CapabilityContext(
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        agent_id="agent-a",
        action_id="action-a",
        idempotency_key="action:action-a",
        metadata={"permissions": ["media.generate"]},
    )


def _client_factory(handler):
    class Client:
        async def __aenter__(self):
            self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return self.client

        async def __aexit__(self, *_args):
            await self.client.aclose()

    return Client


def test_media_plugin_registers_governed_provider_neutral_capabilities() -> None:
    registry = CapabilityExtensionRegistry()
    plugin = MediaGenerationExtension()
    registry.register_extension(plugin)

    assert plugin.providers.provider_ids == ("jimeng", "volcengine_ark")
    for capability_id in ("image.generate", "image.edit", "video.generate"):
        definition, handler = registry.get(capability_id, "1.0.0")
        assert handler is not None
        assert definition.ref.extension_id == "capability-media-generation"
        assert definition.side_effect == "external"
        assert definition.idempotent is False
        assert definition.retryable is False
        assert definition.permissions == ("media.generate",)
        assert definition.configuration_schema["properties"]["default_provider"][
            "enum"
        ] == ["volcengine_ark", "jimeng"]


def test_media_plugin_health_reports_presence_without_exposing_credentials(
    monkeypatch,
) -> None:
    for name in (
        "VOLCENGINE_ARK_API_KEY",
        "ARK_API_KEY",
        "VOLC_ACCESSKEY",
        "VOLCENGINE_ACCESS_KEY_ID",
        "VOLC_SECRETKEY",
        "VOLCENGINE_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    plugin = MediaGenerationExtension()

    missing = plugin.health_checks()
    assert [item["status"] for item in missing] == ["degraded", "degraded"]

    monkeypatch.setenv("VOLCENGINE_ARK_API_KEY", "private-ark-key")
    monkeypatch.setenv("VOLC_ACCESSKEY", "private-access-key")
    monkeypatch.setenv("VOLC_SECRETKEY", "private-secret-key")
    ready = plugin.health_checks()
    serialized = json.dumps(ready)
    assert [item["status"] for item in ready] == ["healthy", "healthy"]
    assert "private-ark-key" not in serialized
    assert "private-access-key" not in serialized
    assert "private-secret-key" not in serialized


def test_volcengine_signature_covers_action_body_and_frozen_identity() -> None:
    headers = sign_openapi_request(
        method="POST",
        url="https://visual.volcengineapi.com",
        params={"Action": "CVSync2AsyncSubmitTask", "Version": "2022-08-31"},
        body=b'{"prompt":"hello"}',
        access_key="test-ak",
        secret_key="test-sk",
        region="cn-north-1",
        service="cv",
        idempotency_key="action:action-a",
        now=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
    )

    assert headers["X-Date"] == "20260810T120000Z"
    assert headers["X-Idempotency-Key"] == "action:action-a"
    assert "Credential=test-ak/20260810/cn-north-1/cv/request" in headers[
        "Authorization"
    ]
    assert "SignedHeaders=content-type;host;x-content-sha256;x-date;x-idempotency-key" in headers[
        "Authorization"
    ]


@pytest.mark.asyncio
async def test_seedream_image_generation_returns_artifact_and_receipt(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VOLCENGINE_ARK_API_KEY", "test-key")

    async def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/images/generations")
        assert request.headers["idempotency-key"] == "action:action-a"
        payload = json.loads(request.content)
        assert payload["model"] == "doubao-seedream-test"
        assert payload["prompt"] == "a quiet studio"
        return httpx.Response(
            200,
            headers={"x-request-id": "request-image-1"},
            json={"data": [{"url": "https://media.example/image.png"}]},
        )

    monkeypatch.setattr(
        "joyhousebot_capability_media_generation.volcengine_ark.TrackedAsyncClient",
        _client_factory(respond),
    )
    registry = CapabilityExtensionRegistry()
    registry.register_extension(MediaGenerationExtension())
    result = await registry.invoke(
        "image.generate",
        {
            "provider": "volcengine_ark",
            "model": "doubao-seedream-test",
            "prompt": "a quiet studio",
        },
        context=_context(),
    )

    assert result.success is True
    assert result.write_receipt.provider_operation_id == "request-image-1"
    assert result.artifacts[0].artifact_type == "media.image"
    assert result.artifacts[0].uri == "https://media.example/image.png"
    assert result.artifacts[0].provenance["model"] == "doubao-seedream-test"
    assert result.artifacts[0].metadata["source_is_ephemeral"] is True
    assert result.artifacts[0].data["source_expires_seconds"] == 86_400


@pytest.mark.asyncio
async def test_seedance_video_is_accepted_then_reconciled(monkeypatch) -> None:
    monkeypatch.setenv("VOLCENGINE_ARK_API_KEY", "test-key")

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["model"] == "doubao-seedance-test"
            assert payload["content"][0]["text"].endswith("--ratio 16:9 --dur 5")
            return httpx.Response(200, json={"id": "seedance-task-1"})
        assert request.url.path.endswith("/contents/generations/tasks/seedance-task-1")
        return httpx.Response(
            200,
            json={
                "id": "seedance-task-1",
                "status": "succeeded",
                "content": {"video_url": "https://media.example/video.mp4"},
                "usage": {"completion_tokens": 100},
            },
        )

    monkeypatch.setattr(
        "joyhousebot_capability_media_generation.volcengine_ark.TrackedAsyncClient",
        _client_factory(respond),
    )
    registry = CapabilityExtensionRegistry()
    registry.register_extension(MediaGenerationExtension())
    _definition, handler = registry.get("video.generate", "1.0.0")
    result = await registry.invoke(
        "video.generate",
        {
            "provider": "volcengine_ark",
            "model": "doubao-seedance-test",
            "prompt": "camera moves forward",
            "ratio": "16:9",
            "duration_seconds": 5,
        },
        context=_context(),
    )

    assert result.success is True and result.status == "accepted"
    assert result.operation["provider_operation_id"] == "seedance-task-1"
    outcome = await handler.reconcile_operation(_context(), result.operation)
    assert outcome.status == "succeeded"
    assert outcome.artifacts[0].artifact_type == "media.video"
    assert outcome.artifacts[0].uri == "https://media.example/video.mp4"


@pytest.mark.asyncio
async def test_media_submission_unknown_enters_manual_reconciliation(monkeypatch) -> None:
    monkeypatch.setenv("VOLCENGINE_ARK_API_KEY", "test-key")

    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "upstream unavailable"}})

    monkeypatch.setattr(
        "joyhousebot_capability_media_generation.volcengine_ark.TrackedAsyncClient",
        _client_factory(respond),
    )
    registry = CapabilityExtensionRegistry()
    registry.register_extension(MediaGenerationExtension())
    _definition, handler = registry.get("video.generate", "1.0.0")
    result = await registry.invoke(
        "video.generate",
        {"provider": "volcengine_ark", "prompt": "camera moves forward"},
        context=_context(),
    )

    assert result.success is True and result.status == "accepted"
    assert result.operation["status"] == "submission_unknown"
    outcome = await handler.reconcile_operation(_context(), result.operation)
    assert outcome.status == "unknown"


@pytest.mark.asyncio
async def test_jimeng_image_task_is_signed_and_reconciled(monkeypatch) -> None:
    monkeypatch.setenv("VOLC_ACCESSKEY", "test-ak")
    monkeypatch.setenv("VOLC_SECRETKEY", "test-sk")

    async def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("HMAC-SHA256 Credential=test-ak/")
        action = request.url.params["Action"]
        payload = json.loads(request.content)
        if action == "CVSync2AsyncSubmitTask":
            assert request.headers["x-idempotency-key"] == "action:action-a"
            assert payload["req_key"] == "t2i_v40_jimeng"
            return httpx.Response(200, json={"code": 10000, "data": {"task_id": "jm-1"}})
        assert action == "CVSync2AsyncGetResult"
        assert payload["task_id"] == "jm-1"
        assert json.loads(payload["req_json"])["return_url"] is True
        return httpx.Response(
            200,
            json={
                "code": 10000,
                "data": {
                    "status": "done",
                    "image_urls": ["https://media.example/jimeng.png"],
                },
            },
        )

    monkeypatch.setattr(
        "joyhousebot_capability_media_generation.jimeng.TrackedAsyncClient",
        _client_factory(respond),
    )
    registry = CapabilityExtensionRegistry()
    registry.register_extension(MediaGenerationExtension())
    _definition, handler = registry.get("image.generate", "1.0.0")
    result = await registry.invoke(
        "image.generate",
        {"provider": "jimeng", "prompt": "一张简洁的产品图"},
        context=_context(),
    )

    assert result.success is True and result.status == "accepted"
    outcome = await handler.reconcile_operation(_context(), result.operation)
    assert outcome.status == "succeeded"
    assert outcome.artifacts[0].metadata["source_is_ephemeral"] is True
    assert outcome.artifacts[0].data["source_expires_seconds"] == 86_400


def test_jimeng_video_model_switches_between_text_and_image_modes() -> None:
    text_key, text_body, text_kind = JimengAdapter._request_body(
        "video.generate",
        {"prompt": "产品旋转", "duration_seconds": 10, "ratio": "9:16"},
        {},
    )
    image_key, image_body, image_kind = JimengAdapter._request_body(
        "video.generate",
        {"prompt": "产品旋转", "image_urls": ["https://media.example/input.png"]},
        {},
    )

    assert (text_key, text_kind, text_body["frames"], text_body["aspect_ratio"]) == (
        "jimeng_t2v_v30",
        "video",
        241,
        "9:16",
    )
    assert (image_key, image_kind, image_body["image_urls"]) == (
        "jimeng_i2v_first_v30",
        "video",
        ["https://media.example/input.png"],
    )
