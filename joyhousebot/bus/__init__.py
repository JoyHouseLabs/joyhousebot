"""Message bus module for decoupled channel-agent communication."""

from joyhousebot.bus.events import InboundMessage, OutboundMessage
from joyhousebot.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
