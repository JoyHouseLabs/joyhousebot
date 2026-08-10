
import pytest
from joyhousebot_channel_slack import SLACK_EXTENSION_MANIFEST, SlackChannelPlugin
from joyhousebot_channel_slack.plugin import ack_emoji_for_slack

from joyhousebot.channels.manager import ChannelManager
from joyhousebot.config.schema import Config, ExtensionsConfig


def test_slack_reaction_aliases_belong_to_the_extension() -> None:
    assert ack_emoji_for_slack(None) == "eyes"
    assert ack_emoji_for_slack("  \U0001f440  ") == "eyes"
    assert ack_emoji_for_slack("\U0001f44d") == "+1"
    assert ack_emoji_for_slack("custom_emoji") == "custom_emoji"


class RecordingAdapter:
    def __init__(self) -> None:
        self.messages = []

    async def handle(self, message):
        self.messages.append(message)
        return None


class SocketClient:
    def __init__(self) -> None:
        self.responses = []

    async def send_socket_mode_response(self, response):
        self.responses.append(response)


def _configured_plugin(adapter=None) -> SlackChannelPlugin:
    plugin = SlackChannelPlugin()
    plugin.configure(
        {"enabled": True, "bot_token": "xoxb-test", "app_token": "xapp-test"},
        adapter or RecordingAdapter(),
    )
    return plugin


def test_slack_extension_has_versioned_channel_manifest() -> None:
    assert SLACK_EXTENSION_MANIFEST.extension_id == "channel-slack"
    assert SLACK_EXTENSION_MANIFEST.extension_types == ("channel",)
    assert SLACK_EXTENSION_MANIFEST.distribution_name == "joyhousebot-channel-slack"
    assert SlackChannelPlugin().extension_manifest is SLACK_EXTENSION_MANIFEST


def test_channel_manager_loads_explicit_slack_extension_entry_point() -> None:
    config = Config(
        extensions=ExtensionsConfig(
            enabled=["channel-slack"],
            discover_entry_points=True,
            settings={
                "channel-slack": {"bot_token": "xoxb-test", "app_token": "xapp-test"}
            },
        )
    )
    manager = ChannelManager(config)
    assert list(manager.plugins) == ["slack"]
    assert manager.registry.source_for("slack") == "entry-point:channel-slack"


@pytest.mark.asyncio
async def test_slack_extension_fails_closed_without_vendor_sdk(monkeypatch) -> None:
    monkeypatch.setattr("joyhousebot_channel_slack.plugin.SLACK_AVAILABLE", False)
    plugin = _configured_plugin()
    await plugin.start()
    assert plugin.is_running is False


def test_slack_group_and_dm_policies_remain_extension_owned() -> None:
    plugin = _configured_plugin()
    plugin._config["dm"] = {
        "enabled": True,
        "policy": "allowlist",
        "allow_from": ["allowed-user"],
    }
    plugin._config["group_policy"] = "allowlist"
    plugin._config["group_allow_from"] = ["allowed-channel"]

    assert plugin._is_allowed("allowed-user", "dm", "im") is True
    assert plugin._is_allowed("other-user", "dm", "im") is False
    assert plugin._is_allowed("user", "allowed-channel", "channel") is True
    assert plugin._is_allowed("user", "other-channel", "channel") is False
