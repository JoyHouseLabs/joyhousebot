import asyncio
import json

import pytest
from porthouse_channel_whatsapp import (
    WHATSAPP_EXTENSION_MANIFEST,
    WhatsAppChannelPlugin,
)

from porthouse.bus.events import OutboundMessage
from porthouse.channels.manager import ChannelManager
from porthouse.config.schema import Config, ExtensionsConfig


class RecordingAdapter:
    def __init__(self) -> None:
        self.messages = []

    async def handle(self, message):
        self.messages.append(message)
        return None


def _configured_plugin(adapter=None) -> WhatsAppChannelPlugin:
    plugin = WhatsAppChannelPlugin()
    plugin.configure(
        {
            "enabled": True,
            "bridge_url": "ws://127.0.0.1:3001",
            "bridge_token": "bridge-test-token",
        },
        adapter or RecordingAdapter(),
    )
    return plugin


def test_whatsapp_extension_has_versioned_channel_manifest() -> None:
    assert WHATSAPP_EXTENSION_MANIFEST.extension_id == "channel-whatsapp"
    assert WHATSAPP_EXTENSION_MANIFEST.extension_types == ("channel",)
    assert WHATSAPP_EXTENSION_MANIFEST.distribution_name == "porthouse-channel-whatsapp"
    assert WhatsAppChannelPlugin().extension_manifest is WHATSAPP_EXTENSION_MANIFEST


def test_channel_manager_loads_explicit_whatsapp_extension_entry_point() -> None:
    config = Config(
        extensions=ExtensionsConfig(
            enabled=["channel-whatsapp"],
            discover_entry_points=True,
            settings={
                "channel-whatsapp": {
                    "bridge_url": "ws://127.0.0.1:3001",
                    "bridge_token": "bridge-test-token",
                }
            },
        )
    )
    manager = ChannelManager(config)
    assert list(manager.plugins) == ["whatsapp"]
    assert manager.registry.source_for("whatsapp") == "entry-point:channel-whatsapp"


@pytest.mark.asyncio
async def test_whatsapp_extension_fails_closed_without_websocket_sdk(monkeypatch) -> None:
    monkeypatch.setattr("porthouse_channel_whatsapp.plugin.WHATSAPP_BRIDGE_AVAILABLE", False)
    plugin = _configured_plugin()
    await plugin.start()
    assert plugin.is_running is False


@pytest.mark.asyncio
async def test_whatsapp_send_waits_for_correlated_bridge_receipt() -> None:
    plugin = _configured_plugin()

    class Socket:
        async def send(self, raw):
            payload = json.loads(raw)
            await asyncio.sleep(0)
            await plugin._handle_bridge_message(
                json.dumps(
                    {
                        "type": "sent",
                        "requestId": payload["requestId"],
                        "messageId": "wa-message-1",
                    }
                )
            )

    plugin._ws = Socket()
    plugin._set_connected(True)
    result = await plugin.send(
        OutboundMessage(
            channel="whatsapp",
            chat_id="15551234567",
            content="hello",
            metadata={"_outbound_id": "outbound-1"},
        )
    )
    assert result.success is True
    assert result.message_id == "wa-message-1"
    assert result.metadata["request_id"] == "outbound-1"


@pytest.mark.asyncio
async def test_whatsapp_inbound_preserves_provider_message_id() -> None:
    adapter = RecordingAdapter()
    plugin = _configured_plugin(adapter)
    await plugin._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "id": "wa-inbound-1",
                "sender": "15551234567@s.whatsapp.net",
                "content": "hello",
                "timestamp": 123,
                "isGroup": False,
            }
        )
    )
    assert len(adapter.messages) == 1
    assert adapter.messages[0].metadata["message_id"] == "wa-inbound-1"
