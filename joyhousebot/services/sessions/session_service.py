"""Shared session domain operations used by HTTP/RPC/CLI adapters."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from joyhousebot.services.errors import ServiceError


def list_sessions_payload(
    *,
    agent: Any,
    config: Any,
    build_sessions_list_payload: Callable[[Any, Any], dict[str, Any]],
) -> dict[str, Any]:
    return build_sessions_list_payload(agent, config)


def resolve_session(*, params: dict[str, Any]) -> dict[str, Any]:
    key = str(params.get("key") or params.get("sessionId") or params.get("session_id") or "")
    if not key:
        raise ServiceError(code="INVALID_REQUEST", message="sessions.resolve requires key")
    return {"ok": True, "key": key}


def preview_session(
    *,
    params: dict[str, Any],
    agent: Any,
    build_chat_history_payload: Callable[[Any, int], dict[str, Any]],
) -> dict[str, Any]:
    session_key = str(params.get("key") or params.get("sessionId") or params.get("session_id") or "rpc:default")
    session = agent.sessions.get_or_create(session_key)
    preview_len = int(params.get("limit") or 20)
    messages = build_chat_history_payload(session, limit=max(1, min(preview_len, 500)))["messages"]
    return {"ok": True, "key": session_key, "messages": messages}


def patch_session(
    *,
    params: dict[str, Any],
    agent: Any,
    apply_session_patch: Callable[[Any, dict[str, Any]], dict[str, Any]],
    now_ms: Callable[[], int],
) -> dict[str, Any]:
    session_key = str(params.get("key") or params.get("sessionId") or params.get("session_id") or "")
    if not session_key:
        raise ServiceError(code="INVALID_REQUEST", message="sessions.patch requires key")
    session = agent.sessions.get_or_create(session_key)
    res = apply_session_patch(session, params)
    if params.get("reset", False):
        session.clear()
        res["changed"] = True
    agent.sessions.save(session)
    return {
        "ok": True,
        "path": str(agent.sessions._get_session_path(session_key)),
        "key": session_key,
        "entry": {"sessionId": session_key, "updatedAt": now_ms()},
        **res,
    }


def reset_session(*, params: dict[str, Any], agent: Any) -> dict[str, Any]:
    session_key = str(params.get("key") or params.get("sessionId") or params.get("session_id") or "")
    if not session_key:
        raise ServiceError(code="INVALID_REQUEST", message="sessions.reset requires key")
    session = agent.sessions.get_or_create(session_key)
    session.clear()
    agent.sessions.save(session)
    return {"ok": True, "key": session_key, "entry": {"sessionId": session_key}}


async def delete_session_payload(
    *,
    params: dict[str, Any],
    delete_session: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    session_key = str(params.get("key") or params.get("sessionId") or params.get("session_id") or "")
    if not session_key:
        raise ServiceError(code="INVALID_REQUEST", message="sessions.delete requires key")
    payload = await delete_session(session_key, agent_id=params.get("agent_id"))
    return {"ok": True, "deleted": bool(payload.get("removed", False))}


async def compact_session(*, params: dict[str, Any], agent: Any) -> dict[str, Any]:
    session_key = str(params.get("key") or params.get("sessionId") or params.get("session_id") or "")
    if not session_key:
        raise ServiceError(code="INVALID_REQUEST", message="sessions.compact requires key")
    session = agent.sessions.get_or_create(session_key)
    if hasattr(agent, "_consolidate_memory"):
        await agent._consolidate_memory(session, archive_all=bool(params.get("archiveAll")))  # type: ignore[attr-defined]
    return {"ok": True, "compacted": True, "key": session_key}


def list_sessions_http(*, agent: Any) -> dict[str, Any]:
    return {"ok": True, "sessions": agent.sessions.list_sessions()}


def get_session_history_http(*, agent: Any, session_key: str) -> dict[str, Any]:
    session = agent.sessions.get_or_create(session_key)
    messages = [
        {"role": m.get("role", "user"), "content": m.get("content", ""), "timestamp": m.get("timestamp")}
        for m in session.messages
    ]
    return {
        "ok": True,
        "key": session_key,
        "messages": messages,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


def delete_session_http(*, agent: Any, session_key: str) -> dict[str, Any]:
    return {"ok": True, "removed": agent.sessions.delete(session_key)}

