from pathlib import Path

import pytest
from porthouse_channel_telegram import (
    TELEGRAM_EXTENSION_MANIFEST,
    TelegramChannelPlugin,
)
from porthouse_channel_telegram.plugin import _markdown_to_telegram_html

from porthouse.channels.manager import ChannelManager
from porthouse.config.schema import Config, ExtensionsConfig


class RecordingAdapter:
    async def handle(self, message):
        return None


def _configured_plugin() -> TelegramChannelPlugin:
    plugin = TelegramChannelPlugin()
    plugin.configure(
        {"enabled": True, "token": "telegram-test-token"},
        RecordingAdapter(),
    )
    return plugin


def test_telegram_extension_has_versioned_channel_manifest() -> None:
    assert TELEGRAM_EXTENSION_MANIFEST.extension_id == "channel-telegram"
    assert TELEGRAM_EXTENSION_MANIFEST.extension_types == ("channel",)
    assert TELEGRAM_EXTENSION_MANIFEST.distribution_name == "porthouse-channel-telegram"
    assert TelegramChannelPlugin().extension_manifest is TELEGRAM_EXTENSION_MANIFEST


def test_channel_manager_loads_explicit_telegram_extension_entry_point() -> None:
    config = Config(
        extensions=ExtensionsConfig(
            enabled=["channel-telegram"],
            discover_entry_points=True,
            settings={"channel-telegram": {"token": "telegram-test-token"}},
        )
    )
    manager = ChannelManager(config)
    assert list(manager.plugins) == ["telegram"]
    assert manager.registry.source_for("telegram") == "entry-point:channel-telegram"


@pytest.mark.asyncio
async def test_telegram_extension_fails_closed_without_vendor_sdk(monkeypatch) -> None:
    monkeypatch.setattr("porthouse_channel_telegram.plugin.TELEGRAM_AVAILABLE", False)
    plugin = _configured_plugin()
    await plugin.start()
    assert plugin.is_running is False


def test_telegram_markdown_conversion_remains_extension_owned() -> None:
    rendered = _markdown_to_telegram_html("**bold** and `code`")
    assert rendered == "<b>bold</b> and <code>code</code>"


def test_telegram_extension_has_no_model_provider_coupling() -> None:
    source = (
        Path(__file__).parents[1]
        / "extensions/channel-telegram/src/porthouse_channel_telegram/plugin.py"
    ).read_text(encoding="utf-8")
    assert "GroqTranscriptionProvider" not in source
    assert "providers.transcription" not in source
    assert "groq_api_key" not in source
