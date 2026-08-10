"""Stable Channel extension API.

Extension packages must import channel contracts from this module instead of
depending on JoyhouseBot runtime, storage, API, or bootstrap internals.
"""

from joyhousebot.bus.events import InboundMessage, OutboundMessage
from joyhousebot.channels.plugins.base import BaseChannelPlugin
from joyhousebot.channels.plugins.types import (
    ChannelCapabilities,
    ChannelMeta,
    ChannelPlugin,
    ChannelStatus,
    ChatType,
    SendResult,
)
from joyhousebot.channels.run_adapter import RunAdapter

__all__ = [
    "BaseChannelPlugin",
    "ChannelCapabilities",
    "ChannelMeta",
    "ChannelPlugin",
    "ChannelStatus",
    "ChatType",
    "InboundMessage",
    "OutboundMessage",
    "RunAdapter",
    "SendResult",
]
