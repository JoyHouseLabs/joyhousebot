"""Tests for the channel plugin system."""

import pytest

from joyhousebot.bus.events import OutboundMessage
from joyhousebot.channels.plugins import (
    ChannelRegistry,
)
from joyhousebot.channels.plugins.base import BaseChannelPlugin
from joyhousebot.channels.plugins.types import (
    ChannelCapabilities,
    ChannelMeta,
    ChatType,
    SendResult,
)


class MockChannelPlugin(BaseChannelPlugin):
    """Mock plugin for testing."""

    def __init__(self, plugin_id: str = "mock"):
        super().__init__()
        self._id = plugin_id

    @property
    def id(self) -> str:
        return self._id

    @property
    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            display_name="Mock",
            description="Mock plugin for testing",
            icon="mock",
            order=999,
        )

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT],
            supports_media=False,
        )

    async def start(self) -> None:
        self._set_running(True)

    async def stop(self) -> None:
        self._set_running(False)

    async def send(self, msg: OutboundMessage) -> SendResult:
        return SendResult(success=True)


class MockRunAdapter:
    def __init__(self):
        self.messages = []

    async def handle(self, message):
        self.messages.append(message)


def test_load_all_builtins():
    """Test loading all built-in channel plugins."""
    registry = ChannelRegistry()

    loaded = registry.load_all_builtins()

    expected_channels = [
        "telegram",
        "discord",
        "slack",
        "whatsapp",
        "feishu",
        "dingtalk",
        "email",
        "qq",
    ]

    for channel_id in expected_channels:
        assert channel_id in loaded, f"Expected {channel_id} to be loaded"
        plugin = registry.get(channel_id)
        assert plugin is not None
        assert plugin.id == channel_id
        assert plugin.meta.display_name
        assert plugin.capabilities


def test_builtin_plugins_have_consistent_interface():
    """Test that all built-in plugins implement the required interface."""
    registry = ChannelRegistry()
    registry.load_all_builtins()

    for channel_id in registry.list_channels():
        plugin = registry.get(channel_id)

        assert hasattr(plugin, "id")
        assert hasattr(plugin, "meta")
        assert hasattr(plugin, "capabilities")
        assert hasattr(plugin, "configure")
        assert hasattr(plugin, "start")
        assert hasattr(plugin, "stop")
        assert hasattr(plugin, "send")

        meta = plugin.meta
        assert meta.display_name
        assert meta.icon

        caps = plugin.capabilities
        assert isinstance(caps.chat_types, list)
        assert len(caps.chat_types) > 0


def test_base_plugin_configure():
    """Test BaseChannelPlugin.configure() stores config and adapter."""
    plugin = MockChannelPlugin()

    adapter = MockRunAdapter()
    config = {"token": "test123", "enabled": True}

    plugin.configure(config, adapter)

    assert plugin._config == config
    assert plugin._run_adapter is adapter


def test_base_plugin_is_running():
    """Test is_running property."""
    plugin = MockChannelPlugin()
    assert plugin.is_running is False

    plugin._set_running(True)
    assert plugin.is_running is True

    plugin._set_running(False)
    assert plugin.is_running is False


@pytest.mark.asyncio
async def test_base_plugin_publish_inbound():
    """Test _publish_inbound method."""
    plugin = MockChannelPlugin()
    adapter = MockRunAdapter()

    plugin.configure({"allow_from": ["user123"]}, adapter)

    await plugin._publish_inbound(
        sender_id="user123",
        chat_id="chat456",
        content="Hello",
    )

    assert len(adapter.messages) == 1
    call_args = adapter.messages[0]
    assert call_args.channel == "mock"
    assert call_args.chat_id == "chat456"
    assert call_args.content == "Hello"


def test_channel_capabilities_defaults():
    """Test ChannelCapabilities default values."""
    caps = ChannelCapabilities(chat_types=[ChatType.DIRECT])

    assert caps.supports_media is False
    assert caps.supports_reactions is False
    assert caps.supports_threads is False
    assert caps.supports_typing is False
    assert caps.supports_polls is False
    assert caps.supports_edit is False
    assert caps.supports_delete is False
    assert caps.text_chunk_limit == 4096
    assert caps.streaming is False


def test_channel_meta_defaults():
    """Test ChannelMeta default values."""
    meta = ChannelMeta(display_name="Test")

    assert meta.display_name == "Test"
    assert meta.description == ""
    assert meta.icon == ""
    assert meta.order == 100


def test_send_result():
    """Test SendResult dataclass."""
    result1 = SendResult(success=True)
    assert result1.success is True
    assert result1.error is None
    assert result1.message_id is None

    result2 = SendResult(success=False, error="Connection failed")
    assert result2.success is False
    assert result2.error == "Connection failed"
