import os

from joyhousebot_capability_vision import VisionCapabilityExtension
from joyhousebot_capability_vision.extension import VisionHandler

from joyhousebot.capabilities import CapabilityExtensionRegistry
from joyhousebot.contracts import CapabilityContext


class _InputAssets:
    async def read_input_asset(self, context, *, asset_id: str, max_bytes: int):  # noqa: ANN001
        assert asset_id == "asset-image"
        assert max_bytes <= 10 * 1024 * 1024
        return {"body": b"png-bytes", "media_type": "image/png", "asset_id": asset_id}


class _Services:
    context = _InputAssets()


class _Provider:
    async def analyze(self, **kwargs):  # noqa: ANN003
        assert kwargs["body"] == b"png-bytes"
        return (
            {
                "observations": [
                    {"kind": "text", "value": "Hello", "confidence": 0.9, "evidence": {"page": 2}},
                ]
            },
            {"input_tokens": 12},
        )


def test_vision_extension_is_versioned_and_requires_scoped_permissions() -> None:
    registry = CapabilityExtensionRegistry()
    registry.register_extension(VisionCapabilityExtension())
    definition, _ = registry.get("vision.understand", "0.1.0")
    assert definition.permissions == ("context.read", "vision.read")
    assert definition.ref.extension_id == "capability-vision"


async def test_vision_reads_only_a_frozen_asset_and_returns_bound_evidence(monkeypatch) -> None:
    monkeypatch.setenv("JOYHOUSEBOT_TEST_VISION_KEY", "test-key")
    result = await VisionHandler(provider=_Provider()).execute(
        CapabilityContext(
            "user",
            "session",
            "run",
            services=_Services(),
            metadata={
                "capability_configuration": {
                    "api_key_env": "JOYHOUSEBOT_TEST_VISION_KEY",
                    "api_url": "https://api.openai.com/v1/chat/completions",
                }
            },
        ),
        {"asset_id": "asset-image", "task": "ocr"},
    )
    assert result.success is True
    observation = result.output["observations"][0]
    assert observation["evidence"]["asset_id"] == "asset-image"
    assert observation["evidence"]["page"] == 2
    assert "png-bytes" not in str(result.output)
    os.environ.pop("JOYHOUSEBOT_TEST_VISION_KEY", None)
