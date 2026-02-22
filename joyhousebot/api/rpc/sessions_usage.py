"""RPC handlers for sessions.* and usage.* methods."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from joyhousebot.services.errors import ServiceError
from joyhousebot.services.sessions.session_service import (
    compact_session,
    delete_session_payload,
    list_sessions_payload,
    patch_session,
    preview_session,
    reset_session,
    resolve_session,
)
from joyhousebot.services.sessions.usage_service import (
    build_usage_logs,
    build_usage_payload,
    build_usage_timeseries,
)

RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_sessions_usage_method(
    *,
    method: str,
    params: dict[str, Any],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    resolve_agent: Callable[[Any], Any],
    build_sessions_list_payload: Callable[[Any, Any], dict[str, Any]],
    build_chat_history_payload: Callable[[Any, int], dict[str, Any]],
    apply_session_patch: Callable[[Any, dict[str, Any]], dict[str, Any]],
    now_ms: Callable[[], int],
    delete_session: Callable[..., Awaitable[dict[str, Any]]],
    config: Any,
    empty_usage_totals: Callable[[], dict[str, Any]],
    session_usage_entry: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
    estimate_tokens: Callable[[str], int],
) -> RpcResult | None:
    """Handle sessions/usage methods. Return None when method is unrelated."""
    if method == "sessions.list":
        agent = resolve_agent(params.get("agent_id"))
        if not agent:
            return False, None, rpc_error("UNAVAILABLE", "agent not initialized", None)
        return True, list_sessions_payload(
            agent=agent,
            config=config,
            build_sessions_list_payload=build_sessions_list_payload,
        ), None

    if method == "sessions.resolve":
        try:
            return True, resolve_session(params=params), None
        except ServiceError as exc:
            return False, None, rpc_error(exc.code, exc.message, None)

    if method == "sessions.preview":
        agent = resolve_agent(params.get("agent_id"))
        if not agent:
            return False, None, rpc_error("UNAVAILABLE", "agent not initialized", None)
        return True, preview_session(
            params=params,
            agent=agent,
            build_chat_history_payload=build_chat_history_payload,
        ), None

    if method == "sessions.patch":
        agent = resolve_agent(params.get("agent_id"))
        if not agent:
            return False, None, rpc_error("UNAVAILABLE", "agent not initialized", None)
        try:
            return True, patch_session(
                params=params,
                agent=agent,
                apply_session_patch=apply_session_patch,
                now_ms=now_ms,
            ), None
        except ServiceError as exc:
            return False, None, rpc_error(exc.code, exc.message, None)

    if method == "sessions.reset":
        agent = resolve_agent(params.get("agent_id"))
        if not agent:
            return False, None, rpc_error("UNAVAILABLE", "agent not initialized", None)
        try:
            return True, reset_session(params=params, agent=agent), None
        except ServiceError as exc:
            return False, None, rpc_error(exc.code, exc.message, None)

    if method == "sessions.delete":
        try:
            return True, await delete_session_payload(
                params=params,
                delete_session=delete_session,
            ), None
        except ServiceError as exc:
            return False, None, rpc_error(exc.code, exc.message, None)

    if method == "sessions.compact":
        agent = resolve_agent(params.get("agent_id"))
        if not agent:
            return False, None, rpc_error("UNAVAILABLE", "agent not initialized", None)
        try:
            return True, await compact_session(params=params, agent=agent), None
        except ServiceError as exc:
            return False, None, rpc_error(exc.code, exc.message, None)

    if method == "sessions.usage":
        agent = resolve_agent(params.get("agent_id"))
        if not agent:
            return False, None, rpc_error("UNAVAILABLE", "agent not initialized", None)
        return True, build_usage_payload(
            params=params,
            now_ms=now_ms,
            agent=agent,
            empty_usage_totals=empty_usage_totals,
            session_usage_entry=session_usage_entry,
        ), None

    if method == "usage.cost":
        agent = resolve_agent(params.get("agent_id"))
        if not agent:
            return False, None, rpc_error("UNAVAILABLE", "agent not initialized", None)
        usage_payload = build_usage_payload(
            params=params,
            now_ms=now_ms,
            agent=agent,
            empty_usage_totals=empty_usage_totals,
            session_usage_entry=session_usage_entry,
        )
        return (
            True,
            {
                "updatedAt": usage_payload.get("updatedAt"),
                "days": len(usage_payload.get("aggregates", {}).get("daily", [])),
                "daily": usage_payload.get("aggregates", {}).get("daily", []),
                "totals": usage_payload.get("totals", empty_usage_totals()),
            },
            None,
        )

    if method == "usage.status":
        agent = resolve_agent(params.get("agent_id"))
        if not agent:
            return True, {"ok": True, "updatedAt": now_ms(), "totals": empty_usage_totals()}, None
        usage_payload = build_usage_payload(
            params=params,
            now_ms=now_ms,
            agent=agent,
            empty_usage_totals=empty_usage_totals,
            session_usage_entry=session_usage_entry,
        )
        totals = usage_payload.get("totals") if isinstance(usage_payload, dict) else empty_usage_totals()
        return True, {"ok": True, "updatedAt": now_ms(), "totals": totals}, None

    if method == "sessions.usage.timeseries":
        key = str(params.get("key") or "")
        agent = resolve_agent(params.get("agent_id"))
        if not agent:
            return False, None, rpc_error("UNAVAILABLE", "agent not initialized", None)
        return True, build_usage_timeseries(
            key=key,
            agent=agent,
            now_ms=now_ms,
            estimate_tokens=estimate_tokens,
        ), None

    if method == "sessions.usage.logs":
        key = str(params.get("key") or "")
        agent = resolve_agent(params.get("agent_id"))
        if not agent:
            return False, None, rpc_error("UNAVAILABLE", "agent not initialized", None)
        return True, build_usage_logs(
            key=key,
            limit=int(params.get("limit") or 500),
            agent=agent,
            estimate_tokens=estimate_tokens,
        ), None

    return None

