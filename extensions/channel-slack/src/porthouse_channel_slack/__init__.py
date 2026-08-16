"""Porthouse optional Slack channel extension."""

from porthouse_channel_slack.plugin import (
    SLACK_EXTENSION_MANIFEST,
    SlackChannelPlugin,
    create_plugin,
)

__all__ = ["SLACK_EXTENSION_MANIFEST", "SlackChannelPlugin", "create_plugin"]
