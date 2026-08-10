"""Tests for the Core Channel extension contract and discovery seam."""

from types import SimpleNamespace

import pytest

from joyhousebot.bus.events import OutboundMessage
from joyhousebot.channels.manager import ChannelManager
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
from joyhousebot.config.schema import Config, ExtensionsConfig
from joyhousebot.contracts.extensions import ExtensionManifest

TEST_BUILD_DIGEST = f"sha256:{'0' * 64}"


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

    @property
    def extension_manifest(self) -> ExtensionManifest:
        return ExtensionManifest(
            extension_id=f"channel-{self.id}",
            version="1.0.0",
            name="Mock",
            extension_types=("channel",),
            build_digest=TEST_BUILD_DIGEST,
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


def test_registry_is_empty_until_an_extension_is_discovered():
    registry = ChannelRegistry()
    assert registry.list_channels() == []


def test_registry_registers_an_explicit_extension():
    registry = ChannelRegistry()
    plugin = MockChannelPlugin()
    registry.register(plugin, source="test")
    assert registry.get("mock") is plugin
    assert registry.source_for("mock") == "test"


def test_registry_discovers_entry_point(monkeypatch):
    registry = ChannelRegistry()
    plugin = MockChannelPlugin("mail-test")

    class Entries(list):
        def select(self, *, group):
            assert group == "joyhousebot.channels"
            return self

    entry = SimpleNamespace(name="channel-mail-test", load=lambda: lambda: plugin)
    monkeypatch.setattr(
        "joyhousebot.extension_discovery.importlib_metadata.entry_points",
        lambda: Entries([entry]),
    )

    assert registry.load_entry_points(enabled=["channel-mail-test"]) == [
        "channel-mail-test"
    ]
    assert registry.get("mail-test") is plugin


def test_registry_validates_extension_manifest():
    class InvalidManifestPlugin(MockChannelPlugin):
        @property
        def extension_manifest(self):
            return ExtensionManifest(
                extension_id="not-a-channel",
                version="1.0.0",
                name="Invalid",
                extension_types=("capability",),
                build_digest=TEST_BUILD_DIGEST,
            )

    plugin = InvalidManifestPlugin()
    with pytest.raises(ValueError, match="does not declare channel"):
        ChannelRegistry().register(plugin)


def test_channel_manager_loads_enabled_email_extension_entry_point():
    config = Config(
        extensions=ExtensionsConfig(
            enabled=["channel-email"],
            discover_entry_points=True,
            settings={"channel-email": {"consent_granted": True}},
        )
    )

    manager = ChannelManager(config)

    assert list(manager.plugins) == ["email"]
    assert manager.registry.source_for("email") == "entry-point:channel-email"


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
