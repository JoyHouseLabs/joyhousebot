"""Tests for the channel plugin system."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

try:
    from unittest.mock import AsyncMock
except ImportError:
    from unittest.mock import MagicMock
    def AsyncMock(*args, **kwargs):
        m = MagicMock(*args, **kwargs)
        m.return_value = MagicMock()
        return m

from joyhousebot.channels.plugins import (
    get_channel_registry,
    reset_channel_registry,
    ChannelPlugin,
)
from joyhousebot.channels.plugins.types import (
    ChannelCapabilities,
    ChannelMeta,
    ChatType,
    SendResult,
)
from joyhousebot.channels.plugins.base import BaseChannelPlugin
from joyhousebot.bus.events import OutboundMessage


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


def test_registry_singleton():
    """Test that get_channel_registry returns a singleton."""
    reset_channel_registry()
    r1 = get_channel_registry()
    r2 = get_channel_registry()
    assert r1 is r2
    reset_channel_registry()


def test_registry_register_and_get():
    """Test registering and retrieving a plugin."""
    reset_channel_registry()
    registry = get_channel_registry()
    
    plugin = MockChannelPlugin("test_plugin")
    registry.register("test_plugin", plugin)
    
    retrieved = registry.get("test_plugin")
    assert retrieved is plugin
    assert retrieved.id == "test_plugin"
    reset_channel_registry()


def test_registry_override():
    """Test that later registrations override earlier ones."""
    reset_channel_registry()
    registry = get_channel_registry()
    
    plugin1 = MockChannelPlugin("override_test")
    plugin1._id = "override_test"
    registry.register("override_test", plugin1)
    
    plugin2 = MockChannelPlugin("override_test")
    registry.register("override_test", plugin2)
    
    retrieved = registry.get("override_test")
    assert retrieved is plugin2
    reset_channel_registry()


def test_registry_list_channels():
    """Test listing registered channels."""
    reset_channel_registry()
    registry = get_channel_registry()
    
    registry.register("channel_a", MockChannelPlugin("channel_a"))
    registry.register("channel_b", MockChannelPlugin("channel_b"))
    
    channels = registry.list_channels()
    assert "channel_a" in channels
    assert "channel_b" in channels
    reset_channel_registry()


def test_load_all_builtins():
    """Test loading all built-in channel plugins."""
    reset_channel_registry()
    registry = get_channel_registry()
    
    loaded = registry.load_all_builtins()
    
    expected_channels = [
        "telegram", "discord", "slack", "whatsapp",
        "feishu", "dingtalk", "mochat", "email", "qq"
    ]
    
    for channel_id in expected_channels:
        assert channel_id in loaded, f"Expected {channel_id} to be loaded"
        plugin = registry.get(channel_id)
        assert plugin is not None
        assert plugin.id == channel_id
        assert plugin.meta.display_name
        assert plugin.capabilities
    
    reset_channel_registry()


def test_builtin_plugins_have_consistent_interface():
    """Test that all built-in plugins implement the required interface."""
    reset_channel_registry()
    registry = get_channel_registry()
    registry.load_all_builtins()
    
    for channel_id in registry.list_channels():
        plugin = registry.get(channel_id)
        
        assert hasattr(plugin, 'id')
        assert hasattr(plugin, 'meta')
        assert hasattr(plugin, 'capabilities')
        assert hasattr(plugin, 'configure')
        assert hasattr(plugin, 'start')
        assert hasattr(plugin, 'stop')
        assert hasattr(plugin, 'send')
        
        meta = plugin.meta
        assert meta.display_name
        assert meta.icon
        
        caps = plugin.capabilities
        assert isinstance(caps.chat_types, list)
        assert len(caps.chat_types) > 0
    
    reset_channel_registry()


def test_base_plugin_configure():
    """Test BaseChannelPlugin.configure() stores config and bus."""
    plugin = MockChannelPlugin()
    
    mock_bus = MagicMock()
    config = {"token": "test123", "enabled": True}
    
    plugin.configure(config, mock_bus)
    
    assert plugin._config == config
    assert plugin._bus is mock_bus


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
    mock_bus = MagicMock()
    mock_bus.publish_inbound = AsyncMock()
    
    plugin.configure({"allow_from": ["user123"]}, mock_bus)
    
    await plugin._publish_inbound(
        sender_id="user123",
        chat_id="chat456",
        content="Hello",
    )
    
    mock_bus.publish_inbound.assert_called_once()
    call_args = mock_bus.publish_inbound.call_args[0][0]
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
