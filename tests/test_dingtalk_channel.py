import pytest
from porthouse_channel_dingtalk import (
    DINGTALK_EXTENSION_MANIFEST,
    DingTalkChannelPlugin,
)

from porthouse.channels.manager import ChannelManager
from porthouse.config.schema import Config, ExtensionsConfig


class RecordingAdapter:
    def __init__(self) -> None:
        self.messages = []

    async def handle(self, message):
        self.messages.append(message)
        return None


def _configured_plugin(adapter=None) -> DingTalkChannelPlugin:
    plugin = DingTalkChannelPlugin()
    plugin.configure(
        {"enabled": True, "client_id": "test-client", "client_secret": "test-secret"},
        adapter or RecordingAdapter(),
    )
    return plugin


def test_dingtalk_extension_has_versioned_channel_manifest() -> None:
    assert DINGTALK_EXTENSION_MANIFEST.extension_id == "channel-dingtalk"
    assert DINGTALK_EXTENSION_MANIFEST.extension_types == ("channel",)
    assert DINGTALK_EXTENSION_MANIFEST.distribution_name == "porthouse-channel-dingtalk"
    assert DingTalkChannelPlugin().extension_manifest is DINGTALK_EXTENSION_MANIFEST


def test_channel_manager_loads_explicit_dingtalk_extension_entry_point() -> None:
    config = Config(
        extensions=ExtensionsConfig(
            enabled=["channel-dingtalk"],
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

    assert list(manager.plugins) == ["dingtalk"]
    assert manager.registry.source_for("dingtalk") == "entry-point:channel-dingtalk"


@pytest.mark.asyncio
async def test_dingtalk_extension_fails_closed_without_vendor_sdk(monkeypatch) -> None:
    monkeypatch.setattr("porthouse_channel_dingtalk.plugin.DINGTALK_AVAILABLE", False)
    plugin = _configured_plugin()

    await plugin.start()

    assert plugin.is_running is False


@pytest.mark.asyncio
async def test_dingtalk_inbound_preserves_provider_message_id() -> None:
    adapter = RecordingAdapter()
    plugin = _configured_plugin(adapter)

    await plugin._on_message(
        "hello",
        "user-1",
        "Alice",
        message_id="message-1",
    )

    assert len(adapter.messages) == 1
    assert adapter.messages[0].metadata["message_id"] == "message-1"
    assert adapter.messages[0].metadata["sender_name"] == "Alice"
