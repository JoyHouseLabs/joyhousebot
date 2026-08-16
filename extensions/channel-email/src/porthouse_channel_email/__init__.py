"""Porthouse official Email channel extension."""

from porthouse_channel_email.plugin import (
    EMAIL_EXTENSION_MANIFEST,
    EmailChannelPlugin,
    create_plugin,
)

__all__ = ["EMAIL_EXTENSION_MANIFEST", "EmailChannelPlugin", "create_plugin"]
