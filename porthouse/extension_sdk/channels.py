"""Stable Channel extension API.

Extension packages must import channel contracts from this module instead of
depending on Porthouse runtime, storage, API, or bootstrap internals.
"""

from porthouse.bus.events import InboundMessage, OutboundMessage
from porthouse.channels.plugins.base import BaseChannelPlugin
from porthouse.channels.plugins.types import (
    ChannelCapabilities,
    ChannelMeta,
    ChannelPlugin,
    ChannelStatus,
    ChatType,
    SendResult,
)
from porthouse.channels.run_adapter import RunAdapter

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
