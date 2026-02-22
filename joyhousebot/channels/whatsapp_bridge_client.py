"""Shared WhatsApp bridge client for API and channel runtime."""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
import json
import time
from typing import Any, AsyncIterator


def _extract_bridge_error_message(raw: Any) -> str:
    if isinstance(raw, dict):
        error = raw.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            if message:
                return message
        text = str(raw.get("message") or "").strip()
        if text:
            return text
    return str(raw or "bridge error")


def _qr_to_data_url(qr_text: str) -> str | None:
    if not qr_text:
        return None
    try:
        import qrcode

        qr = qrcode.QRCode(border=1, box_size=4)
        qr.add_data(qr_text)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        size = len(matrix)
        cell = 4
        width = size * cell
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{width}" viewBox="0 0 {width} {width}">',
            f'<rect width="{width}" height="{width}" fill="white"/>',
        ]
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                if dark:
                    parts.append(f'<rect x="{x*cell}" y="{y*cell}" width="{cell}" height="{cell}" fill="black"/>')
        parts.append("</svg>")
        svg = "".join(parts)
        b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        return f"data:image/svg+xml;base64,{b64}"
    except Exception:
        return None


class WhatsAppBridgeClient:
    """Thin transport wrapper for bridge auth/connection/event waiting."""

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

    async def wait_event(self, *, timeout_ms: int, want_connected_only: bool = False) -> dict[str, Any]:
        deadline = time.time() + (max(1000, timeout_ms) / 1000.0)
        try:
            async with self.connect() as ws:
                while True:
                    remain = deadline - time.time()
                    if remain <= 0:
                        return {"ok": True, "connected": False, "message": "timeout waiting for bridge event"}
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remain, 5.0))
                    data = json.loads(raw) if isinstance(raw, str) else {}
                    t = data.get("type")
                    if t == "error":
                        return {"ok": False, "message": _extract_bridge_error_message(data)}
                    if t == "status":
                        st = str(data.get("status") or "")
                        if st == "connected":
                            return {"ok": True, "connected": True, "message": "connected"}
                        if want_connected_only:
                            continue
                        return {"ok": True, "connected": False, "message": st or "status update"}
                    if t == "qr" and not want_connected_only:
                        qr_text = str(data.get("qr") or "")
                        return {
                            "ok": True,
                            "connected": False,
                            "message": "Scan QR code with WhatsApp Linked Devices",
                            "qr": qr_text,
                            "qrDataUrl": _qr_to_data_url(qr_text),
                        }
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

