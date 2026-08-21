"""Channel Extension contracts and discovery."""

from joyhousebot.channels.extensions.base import BaseChannelExtension
from joyhousebot.channels.extensions.registry import (
    ChannelExtensionRegistry,
)
from joyhousebot.channels.extensions.types import (
    ChannelCapabilities,
    ChannelExtension,
    ChannelMeta,
    ChannelStatus,
    ChatType,
    SendResult,
)

__all__ = [
    "ChatType",
    "ChannelCapabilities",
    "ChannelMeta",
    "ChannelExtension",
    "ChannelStatus",
    "SendResult",
    "BaseChannelExtension",
    "ChannelExtensionRegistry",
]
