import json
from types import SimpleNamespace

import pytest
from joyhousebot_channel_feishu import FEISHU_EXTENSION_MANIFEST, FeishuChannelExtension

from joyhousebot.channels.manager import ChannelManager
from joyhousebot.config.schema import Config, ExtensionsConfig


class RecordingAdapter:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.messages = []

    async def handle(self, message):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary submission failure")
        self.messages.append(message)
        return None


def _configured_extension(adapter=None) -> FeishuChannelExtension:
    extension = FeishuChannelExtension()
    extension.configure(
        {"enabled": True, "app_id": "test-app", "app_secret": "test-secret"},
        adapter or RecordingAdapter(),
    )
    return extension


def _event(message_id: str = "message-1"):
    return SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id=message_id,
                chat_id="chat-1",
                chat_type="p2p",
                message_type="text",
                content=json.dumps({"text": "hello"}),
            ),
            sender=SimpleNamespace(
                sender_type="user",
                sender_id=SimpleNamespace(open_id="user-1"),
            ),
        )
    )


def test_feishu_extension_has_versioned_channel_manifest() -> None:
    assert FEISHU_EXTENSION_MANIFEST.extension_id == "channel-feishu"
    assert FEISHU_EXTENSION_MANIFEST.extension_types == ("channel",)
    assert FEISHU_EXTENSION_MANIFEST.distribution_name == "joyhousebot-channel-feishu"
    assert FeishuChannelExtension().extension_manifest is FEISHU_EXTENSION_MANIFEST


def test_channel_manager_loads_explicit_feishu_extension_entry_point() -> None:
    config = Config(
        extensions=ExtensionsConfig(
            allowed_ids=["channel-feishu"],
            discover_entry_points=True,
            settings={"channel-feishu": {"app_id": "test-app", "app_secret": "secret"}},
        )
    )
    manager = ChannelManager(config)
    assert list(manager.extensions) == ["feishu"]
    assert manager.registry.source_for("feishu") == "entry-point:channel-feishu"


@pytest.mark.asyncio
async def test_feishu_extension_fails_closed_without_vendor_sdk(monkeypatch) -> None:
    monkeypatch.setattr("joyhousebot_channel_feishu.extension.FEISHU_AVAILABLE", False)
    extension = _configured_extension()
    await extension.start()
    assert extension.is_running is False


@pytest.mark.asyncio
async def test_feishu_only_remembers_message_after_runtime_accepts_it() -> None:
    adapter = RecordingAdapter(fail_once=True)
    extension = _configured_extension(adapter)

    await extension._on_message(_event())
    assert list(extension._processed_message_ids) == []

    await extension._on_message(_event())
    assert list(extension._processed_message_ids) == ["message-1"]
    assert len(adapter.messages) == 1
    assert adapter.messages[0].metadata["message_id"] == "message-1"
