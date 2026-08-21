"""Stable Channel extension API.

Extension packages must import channel contracts from this module instead of
depending on joyhousebot runtime, storage, API, or bootstrap internals.
"""

from joyhousebot.bus.events import InboundMessage, OutboundMessage
from joyhousebot.channels.extensions.base import BaseChannelExtension
from joyhousebot.channels.extensions.types import (
    ChannelCapabilities,
    ChannelExtension,
    ChannelMeta,
    ChannelStatus,
    ChatType,
    SendResult,
)
from joyhousebot.channels.run_adapter import RunAdapter

__all__ = [
    "BaseChannelExtension",
    "ChannelCapabilities",
    "ChannelMeta",
    "ChannelExtension",
    "ChannelStatus",
    "ChatType",
    "InboundMessage",
    "OutboundMessage",
    "RunAdapter",
    "SendResult",
]
