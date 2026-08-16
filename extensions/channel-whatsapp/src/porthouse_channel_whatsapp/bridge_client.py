"""Authenticated WebSocket client for the optional WhatsApp bridge."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

try:
    import websockets

    WHATSAPP_BRIDGE_AVAILABLE = True
except ImportError:
    websockets = None
    WHATSAPP_BRIDGE_AVAILABLE = False


class WhatsAppBridgeClient:
    """Thin transport wrapper for an authenticated bridge connection."""

    def __init__(self, *, bridge_url: str, bridge_token: str = ""):
        self.bridge_url = bridge_url
        self.bridge_token = bridge_token

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[Any]:
        if not WHATSAPP_BRIDGE_AVAILABLE:
            raise RuntimeError(
                "WebSocket SDK not installed. Install porthouse-channel-whatsapp"
            )

        async with websockets.connect(self.bridge_url) as ws:
            if self.bridge_token:
                await ws.send(json.dumps({"type": "auth", "token": self.bridge_token}))
            yield ws
