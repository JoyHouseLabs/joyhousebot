import pytest
from joyhousebot_channel_dingtalk import (
    DINGTALK_EXTENSION_MANIFEST,
    DingTalkChannelExtension,
)

from joyhousebot.channels.manager import ChannelManager
from joyhousebot.config.schema import Config, ExtensionsConfig


class RecordingAdapter:
    def __init__(self) -> None:
        self.messages = []

    async def handle(self, message):
        self.messages.append(message)
        return None


def _configured_extension(adapter=None) -> DingTalkChannelExtension:
    extension = DingTalkChannelExtension()
    extension.configure(
        {"enabled": True, "client_id": "test-client", "client_secret": "test-secret"},
        adapter or RecordingAdapter(),
    )
    return extension


def test_dingtalk_extension_has_versioned_channel_manifest() -> None:
    assert DINGTALK_EXTENSION_MANIFEST.extension_id == "channel-dingtalk"
    assert DINGTALK_EXTENSION_MANIFEST.extension_types == ("channel",)
    assert DINGTALK_EXTENSION_MANIFEST.distribution_name == "joyhousebot-channel-dingtalk"
    assert DingTalkChannelExtension().extension_manifest is DINGTALK_EXTENSION_MANIFEST


def test_channel_manager_loads_explicit_dingtalk_extension_entry_point() -> None:
    config = Config(
        extensions=ExtensionsConfig(
            allowed_ids=["channel-dingtalk"],
            discover_entry_points=True,
            settings={
                "channel-dingtalk": {
                    "client_id": "test-client",
                    "client_secret": "test-secret",
                }
            },
        )
    )

    manager = ChannelManager(config)

    assert list(manager.extensions) == ["dingtalk"]
    assert manager.registry.source_for("dingtalk") == "entry-point:channel-dingtalk"


@pytest.mark.asyncio
async def test_dingtalk_extension_fails_closed_without_vendor_sdk(monkeypatch) -> None:
    monkeypatch.setattr("joyhousebot_channel_dingtalk.extension.DINGTALK_AVAILABLE", False)
    extension = _configured_extension()

    await extension.start()

    assert extension.is_running is False


@pytest.mark.asyncio
async def test_dingtalk_inbound_preserves_provider_message_id() -> None:
    adapter = RecordingAdapter()
    extension = _configured_extension(adapter)

    await extension._on_message(
        "hello",
        "user-1",
        "Alice",
        message_id="message-1",
    )

    assert len(adapter.messages) == 1
    assert adapter.messages[0].metadata["message_id"] == "message-1"
    assert adapter.messages[0].metadata["sender_name"] == "Alice"
