from contextlib import asynccontextmanager
import json

import pytest

from joyhousebot.channels.whatsapp_bridge_client import WhatsAppBridgeClient


class _FakeWs:
    def __init__(self, frames: list[dict[str, object] | str]):
        self._frames = list(frames)

    async def recv(self) -> str:
        if not self._frames:
            await pytest.fail("unexpected recv")
        frame = self._frames.pop(0)
        return frame if isinstance(frame, str) else json.dumps(frame)

    async def send(self, _: str) -> None:
        return


@pytest.mark.asyncio
async def test_wait_event_returns_qr_payload():
    client = WhatsAppBridgeClient(bridge_url="ws://example", bridge_token="x")

    @asynccontextmanager
    async def _fake_connect():
        yield _FakeWs([{"type": "qr", "qr": "abc"}])

    client.connect = _fake_connect  # type: ignore[method-assign]
    out = await client.wait_event(timeout_ms=2000, want_connected_only=False)
    assert out["ok"] is True
    assert out["connected"] is False
    assert out["qr"] == "abc"
    assert "qrDataUrl" in out


@pytest.mark.asyncio
async def test_wait_event_handles_structured_error_payload():
    client = WhatsAppBridgeClient(bridge_url="ws://example", bridge_token="x")

    @asynccontextmanager
    async def _fake_connect():
        yield _FakeWs([{"type": "error", "error": {"code": "AUTH_FAILED", "message": "bad token"}}])

    client.connect = _fake_connect  # type: ignore[method-assign]
    out = await client.wait_event(timeout_ms=2000, want_connected_only=False)
    assert out["ok"] is False
    assert "bad token" in str(out["message"])

