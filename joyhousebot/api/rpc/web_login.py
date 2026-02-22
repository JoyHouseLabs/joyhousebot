"""RPC handlers for WhatsApp web login flow."""

from __future__ import annotations

from typing import Any, Callable

from joyhousebot.channels.whatsapp_bridge_client import WhatsAppBridgeClient


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_web_login_method(
    *,
    method: str,
    params: dict[str, Any],
    config: Any,
    save_persistent_state: Callable[[str, Any], None],
    now_ms: Callable[[], int],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
) -> RpcResult | None:
    """Handle web.login.* RPC methods. Return None when method is not handled."""
    if method == "web.login.start":
        channel_cfg = config.channels.whatsapp
        timeout_ms = int(params.get("timeoutMs") or 30000)
        bridge = WhatsAppBridgeClient(
            bridge_url=channel_cfg.bridge_url,
            bridge_token=channel_cfg.bridge_token,
        )
        res = await bridge.wait_event(timeout_ms=timeout_ms, want_connected_only=False)
        if not res.get("ok"):
            return False, None, rpc_error("UNAVAILABLE", str(res.get("message") or "bridge unavailable"), None)
        if isinstance(res.get("qr"), str):
            save_persistent_state(
                "rpc.whatsapp_login",
                {"lastQr": res.get("qr"), "updatedAtMs": now_ms(), "connected": bool(res.get("connected"))},
            )
        return (
            True,
            {
                "message": res.get("message"),
                "qrDataUrl": res.get("qrDataUrl"),
                "connected": res.get("connected", False),
            },
            None,
        )

    if method == "web.login.wait":
        channel_cfg = config.channels.whatsapp
        timeout_ms = int(params.get("timeoutMs") or 120000)
        bridge = WhatsAppBridgeClient(
            bridge_url=channel_cfg.bridge_url,
            bridge_token=channel_cfg.bridge_token,
        )
        res = await bridge.wait_event(timeout_ms=timeout_ms, want_connected_only=True)
        if not res.get("ok"):
            return False, None, rpc_error("UNAVAILABLE", str(res.get("message") or "bridge unavailable"), None)
        save_persistent_state(
            "rpc.whatsapp_login",
            {"lastQr": None, "updatedAtMs": now_ms(), "connected": bool(res.get("connected"))},
        )
        return True, {"message": res.get("message"), "connected": bool(res.get("connected"))}, None

    return None

