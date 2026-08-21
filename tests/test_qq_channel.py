from types import SimpleNamespace

import pytest
from joyhousebot_channel_qq import QQ_EXTENSION_MANIFEST, QQChannelExtension

from joyhousebot.channels.manager import ChannelManager
from joyhousebot.config.schema import Config, ExtensionsConfig
from joyhousebot.extension_sdk.channels import OutboundMessage


class RecordingAdapter:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.messages = []

    async def handle(self, message):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary runtime submission failure")
        self.messages.append(message)
        return None


def _configured_extension(adapter=None) -> QQChannelExtension:
    extension = QQChannelExtension()
    extension.configure(
        {"enabled": True, "app_id": "test-app", "secret": "test-secret"},
        adapter or RecordingAdapter(),
    )
    return extension


def test_qq_extension_has_versioned_channel_manifest() -> None:
    assert QQ_EXTENSION_MANIFEST.extension_id == "channel-qq"
    assert QQ_EXTENSION_MANIFEST.extension_types == ("channel",)
    assert QQ_EXTENSION_MANIFEST.distribution_name == "joyhousebot-channel-qq"
    assert QQChannelExtension().extension_manifest is QQ_EXTENSION_MANIFEST


def test_channel_manager_loads_explicit_qq_extension_entry_point() -> None:
    config = Config(
        extensions=ExtensionsConfig(
            allowed_ids=["channel-qq"],
            discover_entry_points=True,
            settings={"channel-qq": {"app_id": "test-app", "secret": "test-secret"}},
        )
    )

    manager = ChannelManager(config)

    assert list(manager.extensions) == ["qq"]
    assert manager.registry.source_for("qq") == "entry-point:channel-qq"


@pytest.mark.asyncio
async def test_qq_extension_fails_closed_without_vendor_sdk(monkeypatch) -> None:
    monkeypatch.setattr("joyhousebot_channel_qq.extension.QQ_AVAILABLE", False)
    extension = _configured_extension()

    await extension.start()

    assert extension.is_running is False


@pytest.mark.asyncio
async def test_qq_send_requires_initialized_client() -> None:
    result = await _configured_extension().send(
        OutboundMessage(channel="qq", chat_id="openid", content="hello")
    )
    assert result.success is False
    assert result.error == "client_not_initialized"


@pytest.mark.asyncio
async def test_qq_message_is_only_remembered_after_runtime_accepts_it() -> None:
    adapter = RecordingAdapter(fail_once=True)
    extension = _configured_extension(adapter)
    message = SimpleNamespace(
        id="message-1",
        author=SimpleNamespace(id="user-1"),
        content="hello",
    )

    await extension._on_message(message)
    assert list(extension._processed_ids) == []

    await extension._on_message(message)
    assert list(extension._processed_ids) == ["message-1"]
    assert len(adapter.messages) == 1
    assert adapter.messages[0].metadata["message_id"] == "message-1"
