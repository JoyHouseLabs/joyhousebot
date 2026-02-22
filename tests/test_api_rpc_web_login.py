import pytest

from joyhousebot.api.rpc import web_login
from joyhousebot.config.schema import Config


class _FakeBridgeClient:
    def __init__(self, *, bridge_url: str, bridge_token: str):
        self.bridge_url = bridge_url
        self.bridge_token = bridge_token

    async def wait_event(self, *, timeout_ms: int, want_connected_only: bool = False):
        if want_connected_only:
            return {"ok": True, "connected": True, "message": "connected"}
        return {
            "ok": True,
            "connected": False,
            "message": "Scan QR code with WhatsApp Linked Devices",
            "qr": "test-qr",
            "qrDataUrl": "data:image/svg+xml;base64,abc",
        }


@pytest.mark.asyncio
async def test_try_handle_web_login_start_saves_qr(monkeypatch):
    cfg = Config()
    saves: list[tuple[str, dict]] = []
    monkeypatch.setattr(web_login, "WhatsAppBridgeClient", _FakeBridgeClient)

    result = await web_login.try_handle_web_login_method(
        method="web.login.start",
        params={"timeoutMs": 12345},
        config=cfg,
        save_persistent_state=lambda key, value: saves.append((key, value)),
        now_ms=lambda: 111,
        rpc_error=lambda code, message, data=None: {"code": code, "message": message, "data": data},
    )

    assert result is not None
    ok, payload, err = result
    assert ok is True
    assert err is None
    assert payload["qrDataUrl"].startswith("data:image")
    assert saves and saves[0][0] == "rpc.whatsapp_login"


@pytest.mark.asyncio
async def test_try_handle_web_login_wait_connected(monkeypatch):
    cfg = Config()
    monkeypatch.setattr(web_login, "WhatsAppBridgeClient", _FakeBridgeClient)

    result = await web_login.try_handle_web_login_method(
        method="web.login.wait",
        params={},
        config=cfg,
        save_persistent_state=lambda *_: None,
        now_ms=lambda: 222,
        rpc_error=lambda code, message, data=None: {"code": code, "message": message, "data": data},
    )
    assert result is not None
    ok, payload, err = result
    assert ok is True
    assert err is None
    assert payload["connected"] is True

