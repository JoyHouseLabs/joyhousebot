import pytest
from joyhousebot_channel_discord import DISCORD_EXTENSION_MANIFEST, DiscordChannelExtension

from joyhousebot.channels.manager import ChannelManager
from joyhousebot.config.schema import Config, ExtensionsConfig


class RecordingAdapter:
    async def handle(self, message):
        return None


def _configured_extension() -> DiscordChannelExtension:
    extension = DiscordChannelExtension()
    extension.configure(
        {"enabled": True, "token": "discord-test-token"},
        RecordingAdapter(),
    )
    return extension


def test_discord_extension_has_versioned_channel_manifest() -> None:
    assert DISCORD_EXTENSION_MANIFEST.extension_id == "channel-discord"
    assert DISCORD_EXTENSION_MANIFEST.extension_types == ("channel",)
    assert DISCORD_EXTENSION_MANIFEST.distribution_name == "joyhousebot-channel-discord"
    assert DiscordChannelExtension().extension_manifest is DISCORD_EXTENSION_MANIFEST


def test_channel_manager_loads_explicit_discord_extension_entry_point() -> None:
    config = Config(
        extensions=ExtensionsConfig(
            allowed_ids=["channel-discord"],
            discover_entry_points=True,
            settings={"channel-discord": {"token": "discord-test-token"}},
        )
    )
    manager = ChannelManager(config)
    assert list(manager.extensions) == ["discord"]
    assert manager.registry.source_for("discord") == "entry-point:channel-discord"


@pytest.mark.asyncio
async def test_discord_extension_fails_closed_without_websocket_sdk(monkeypatch) -> None:
    monkeypatch.setattr("joyhousebot_channel_discord.extension.DISCORD_AVAILABLE", False)
    extension = _configured_extension()
    await extension.start()
    assert extension.is_running is False


@pytest.mark.asyncio
async def test_discord_identify_payload_remains_extension_owned() -> None:
    class Socket:
        def __init__(self):
            self.payload = None

        async def send(self, payload):
            self.payload = payload

    extension = _configured_extension()
    socket = Socket()
    extension._ws = socket
    await extension._identify()
    assert '"op": 2' in socket.payload
    assert '"token": "discord-test-token"' in socket.payload
