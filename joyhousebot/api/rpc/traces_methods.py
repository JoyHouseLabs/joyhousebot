"""RPC handlers for agent trace observability (traces.list, traces.get)."""

from __future__ import annotations

from typing import Any, Callable


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_traces_method(
    *,
    method: str,
    params: dict[str, Any],
    get_store: Callable[[], Any],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
) -> RpcResult | None:
    """Handle traces.list and traces.get for agent run trace observability."""
    if method == "traces.list":
        session_key = params.get("sessionKey") or params.get("session_key")
        if isinstance(session_key, str):
            session_key = session_key.strip() or None
        else:
            session_key = None
        limit = int(params.get("limit") or 50)
        limit = max(1, min(limit, 200))
        cursor = params.get("cursor")
        if isinstance(cursor, str):
            cursor = cursor.strip() or None
        else:
            cursor = None
        store = get_store()
        items, next_cursor = store.list_agent_traces(
            session_key=session_key,
            limit=limit,
            cursor=cursor,
        )
        payload = {"items": items}
        if next_cursor is not None:
            payload["nextCursor"] = next_cursor
        return True, payload, None
    if method == "traces.get":
        trace_id = params.get("traceId") or params.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id.strip():
            return False, None, rpc_error("INVALID_REQUEST", "traces.get requires traceId", None)
        store = get_store()
        trace = store.get_agent_trace(trace_id.strip())
        if trace is None:
            return False, None, rpc_error("NOT_FOUND", "trace not found", {"traceId": trace_id})
        return True, trace, None
    return None
