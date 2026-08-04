"""Transport-to-runtime adapter contract.

Channel connectors only know how to receive and send provider messages.  This
adapter is the single boundary that turns an inbound message into a durable
AgentOptions run and turns its terminal result back into an outbound message.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from joyhousebot.bus.events import InboundMessage
from joyhousebot.runtime.models import AgentResult


@runtime_checkable
class RunAdapter(Protocol):
    async def handle(self, message: InboundMessage) -> AgentResult | None:
        """Submit and await one transport message."""


__all__ = ["RunAdapter"]
