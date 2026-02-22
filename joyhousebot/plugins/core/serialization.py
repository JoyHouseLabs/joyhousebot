"""Serialization helpers for bridge RPC frames."""

from __future__ import annotations

import json
from typing import Any

from .protocol import RpcError, RpcRequest, RpcResponse
from .types import PluginHostError


def safe_dict(value: Any) -> dict[str, Any]:
    """Return the value when dict-like, otherwise an empty dict."""
    return value if isinstance(value, dict) else {}


def encode_request_line(request: RpcRequest) -> str:
    """Encode a request frame into one line of JSON."""
    payload = {"id": request.id, "method": request.method, "params": request.params}
    return json.dumps(payload, ensure_ascii=False)


def normalize_rpc_error(error: Any) -> RpcError:
    """Normalize unknown error payloads into RpcError."""
    row = safe_dict(error)
    data = row.get("data")
    return RpcError(
        code=str(row.get("code") or "RPC_ERROR"),
        message=str(row.get("message") or "rpc failed"),
        data=data if isinstance(data, dict) else None,
    )


def decode_response_payload(payload: Any, *, fallback_id: str = "unknown") -> RpcResponse:
    """Decode raw dict payload into normalized RpcResponse."""
    row = safe_dict(payload)
    req_id = str(row.get("id") or fallback_id)
    ok = bool(row.get("ok"))
    if ok:
        return RpcResponse(id=req_id, ok=True, result=row.get("result"))
    return RpcResponse(id=req_id, ok=False, error=normalize_rpc_error(row.get("error")))


def to_plugin_host_error(response: RpcResponse, *, fallback_method: str) -> PluginHostError:
    """Convert an error response to PluginHostError."""
    err = response.error or RpcError(code="RPC_ERROR", message=f"{fallback_method} failed")
    return PluginHostError(err.code, err.message, err.data)

