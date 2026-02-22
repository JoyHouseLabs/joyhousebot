"""RPC handlers for health/status heartbeat methods."""

from __future__ import annotations

from typing import Any, Awaitable, Callable


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_health_status_method(
    *,
    method: str,
    params: dict[str, Any],
    control_overview: Callable[[], Awaitable[dict[str, Any]]],
    run_rpc_shadow: Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[None]],
    load_persistent_state: Callable[[str, Any], Any],
) -> RpcResult | None:
    """Handle health/status/last-heartbeat methods."""
    if method == "health":
        payload = await control_overview()
        await run_rpc_shadow(method, params, payload)
        return True, payload, None
    if method == "status":
        payload = await control_overview()
        await run_rpc_shadow(method, params, payload)
        return True, payload, None
    if method == "last-heartbeat":
        ts = load_persistent_state("rpc.last_heartbeat", {"ts": None}).get("ts")
        return True, {"ok": True, "ts": ts}, None
    return None

