"""Lightweight RPC client helpers for gateway /ws/rpc."""

from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import urlparse, urlunparse

from websocket import WebSocketTimeoutException, create_connection

from joyhousebot.cli.shared.http_utils import get_gateway_base_url


def _gateway_rpc_ws_url() -> str:
    base = get_gateway_base_url()
    parsed = urlparse(base)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_parsed = parsed._replace(scheme=scheme, path="/ws/rpc", params="", query="", fragment="")
    return urlunparse(ws_parsed)


def rpc_call(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    role: str = "operator",
    scopes: list[str] | None = None,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    """Call a single RPC method through /ws/rpc and return payload."""
    ws = create_connection(_gateway_rpc_ws_url(), timeout=max(1.0, timeout_s))
    try:
        connect_id = f"req_{uuid.uuid4().hex[:12]}"
        ws.send(
            json.dumps(
                {
                    "type": "req",
                    "id": connect_id,
                    "method": "connect",
                    "params": {
                        "role": role,
                        "clientId": "joyhousebot-cli",
                        "scopes": scopes
                        or ["operator.read", "operator.write", "operator.admin"],
                    },
                }
            )
        )
        _wait_response(ws, connect_id, timeout_s=timeout_s)

        req_id = f"req_{uuid.uuid4().hex[:12]}"
        ws.send(
            json.dumps(
                {
                    "type": "req",
                    "id": req_id,
                    "method": method,
                    "params": params or {},
                }
            )
        )
        return _wait_response(ws, req_id, timeout_s=timeout_s)
    finally:
        ws.close()


def _wait_response(ws: Any, req_id: str, *, timeout_s: float) -> dict[str, Any]:
    ws.settimeout(max(1.0, timeout_s))
    while True:
        try:
            raw = ws.recv()
        except WebSocketTimeoutException as exc:
            raise RuntimeError(f"RPC timeout waiting response for {req_id}") from exc
        frame = json.loads(raw)
        if not isinstance(frame, dict):
            continue
        if frame.get("type") != "res":
            continue
        if str(frame.get("id") or "") != req_id:
            continue
        if frame.get("ok"):
            payload = frame.get("payload")
            return payload if isinstance(payload, dict) else {"value": payload}
        error = frame.get("error") if isinstance(frame.get("error"), dict) else {}
        message = str(error.get("message") or "RPC request failed")
        code = str(error.get("code") or "RPC_ERROR")
        raise RuntimeError(f"{code}: {message}")
