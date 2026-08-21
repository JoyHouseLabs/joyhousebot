"""joyhousebot optional Slack channel extension."""

from joyhousebot_channel_slack.extension import (
    SLACK_EXTENSION_MANIFEST,
    SlackChannelExtension,
    create_extension,
)

__all__ = ["SLACK_EXTENSION_MANIFEST", "SlackChannelExtension", "create_extension"]
