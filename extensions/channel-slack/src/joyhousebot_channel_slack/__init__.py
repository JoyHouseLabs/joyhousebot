"""JoyhouseBot optional Slack channel extension."""

from joyhousebot_channel_slack.plugin import (
    SLACK_EXTENSION_MANIFEST,
    SlackChannelPlugin,
    create_plugin,
)

__all__ = ["SLACK_EXTENSION_MANIFEST", "SlackChannelPlugin", "create_plugin"]
