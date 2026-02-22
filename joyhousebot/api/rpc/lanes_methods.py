"""RPC handlers for lane queue observability (lanes.status, lanes.list)."""

from __future__ import annotations

from typing import Any, Callable

from joyhousebot.services.lanes import lane_list_all, lane_status


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_lanes_method(
    *,
    method: str,
    params: dict[str, Any],
    app_state: dict[str, Any],
    now_ms: Callable[[], int],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
) -> RpcResult | None:
    """Handle lanes.status and lanes.list for queue observability."""
    if method == "lanes.status":
        session_key = params.get("sessionKey") or params.get("session_key")
        if isinstance(session_key, str):
            session_key = session_key.strip() or None
        else:
            session_key = None
        payload = lane_status(app_state, session_key, now_ms())
        return True, payload, None
    if method == "lanes.list":
        payload = {"lanes": lane_list_all(app_state, now_ms())}
        return True, payload, None
    return None
