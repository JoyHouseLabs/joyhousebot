"""Message bus module for decoupled channel-agent communication."""

from porthouse.bus.events import InboundMessage, OutboundMessage

__all__ = ["InboundMessage", "OutboundMessage"]
