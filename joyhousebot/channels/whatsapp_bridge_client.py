"""Shared WhatsApp bridge client for API and channel runtime."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


class WhatsAppBridgeClient:
    """Thin transport wrapper for an authenticated bridge connection."""

    def __init__(self, *, bridge_url: str, bridge_token: str = ""):
        self.bridge_url = bridge_url
        self.bridge_token = bridge_token

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[Any]:
        import websockets

        async with websockets.connect(self.bridge_url) as ws:
            if self.bridge_token:
                await ws.send(json.dumps({"type": "auth", "token": self.bridge_token}))
            yield ws
