"""Shared chat runtime service for RPC/HTTP adapters."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable

from joyhousebot.services.errors import ServiceError


async def try_handle_chat_runtime(
    *,
    method: str,
    params: dict[str, Any],
    register_agent_job: Callable[[str, str | None], bool],
    get_running_run_id_for_session: Callable[[str], str | None],
    complete_agent_job: Callable[[str], None] | Callable[[str, str], None] | Callable[[str, str, str], None],
    wait_agent_job: Callable[[str], Awaitable[dict[str, Any] | None]] | Callable[..., Awaitable[dict[str, Any] | None]],
    chat: Callable[[Any], Awaitable[dict[str, Any]]],
    chat_message_cls: type,
    resolve_agent: Callable[[Any], Any | None],
    build_chat_history_payload: Callable[[Any, int], dict[str, Any]],
    now_iso: Callable[[], str],
    now_ms: Callable[[], int],
    emit_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    fanout_chat_to_subscribed_nodes: Callable[[str, dict[str, Any]], Awaitable[None]],
    broadcast_rpc_event: Callable[[str, Any, set[str] | None], Awaitable[None]] | None = None,
    lane_can_run: Callable[[str], bool] | None = None,
    lane_enqueue: Callable[[str, str, dict[str, Any]], str] | None = None,
    persist_trace: Callable[[str, str, str, str | None], None] | None = None,
    request_abort: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    if method in {"chat.send", "agent"}:
        return await _chat_send_or_agent(
            method=method,
            params=params,
            register_agent_job=register_agent_job,
            get_running_run_id_for_session=get_running_run_id_for_session,
            complete_agent_job=complete_agent_job,
            wait_agent_job=wait_agent_job,
            chat=chat,
            chat_message_cls=chat_message_cls,
            now_ms=now_ms,
            emit_event=emit_event,
            fanout_chat_to_subscribed_nodes=fanout_chat_to_subscribed_nodes,
            broadcast_rpc_event=broadcast_rpc_event,
            lane_can_run=lane_can_run,
            lane_enqueue=lane_enqueue,
            persist_trace=persist_trace,
        )
    if method == "agent.wait":
        return await _agent_wait(params=params, wait_agent_job=wait_agent_job)
    if method == "chat.inject":
        return _chat_inject(params=params, resolve_agent=resolve_agent, now_iso=now_iso)
    if method == "chat.abort":
        return await _chat_abort(params=params, emit_event=emit_event, request_abort=request_abort)
    if method == "chat.history":
        return _chat_history(
            params=params,
            resolve_agent=resolve_agent,
            build_chat_history_payload=build_chat_history_payload,
        )
    return None


async def _chat_send_or_agent(
    *,
    method: str,
    params: dict[str, Any],
    register_agent_job: Callable[[str, str | None], bool],
    get_running_run_id_for_session: Callable[[str], str | None],
    complete_agent_job: Callable[[str], None] | Callable[[str, str], None] | Callable[[str, str, str], None],
    wait_agent_job: Callable[[str], Awaitable[dict[str, Any] | None]] | Callable[..., Awaitable[dict[str, Any] | None]],
    chat: Callable[[Any], Awaitable[dict[str, Any]]],
    chat_message_cls: type,
    now_ms: Callable[[], int],
    emit_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    fanout_chat_to_subscribed_nodes: Callable[[str, dict[str, Any]], Awaitable[None]],
    broadcast_rpc_event: Callable[[str, Any, set[str] | None], Awaitable[None]] | None = None,
    lane_can_run: Callable[[str], bool] | None = None,
    lane_enqueue: Callable[[str, str, dict[str, Any]], str] | None = None,
    persist_trace: Callable[[str, str, str, str | None], None] | None = None,
) -> dict[str, Any]:
    message = str(params.get("message") or params.get("text") or "").strip()
    if not message:
        raise ServiceError(code="INVALID_REQUEST", message="chat.send requires message")
    session_id = str(params.get("sessionKey") or params.get("sessionId") or params.get("session_id") or "main")
    agent_id = params.get("agentId") or params.get("agent_id")
    run_id = str(params.get("idempotencyKey") or uuid.uuid4().hex[:12])
    expect_final = bool(params.get("expectFinal", False))
    timeout_ms = int(params.get("timeoutMs") or 30000)

    # Lane queue: when session is busy, enqueue instead of returning in_flight (strict queue).
    if lane_can_run is not None and lane_enqueue is not None and not lane_can_run(session_id):
        result = lane_enqueue(session_id, run_id, params)
        if result.get("status") == "queued":
            return {
                "status": "queued",
                "ok": True,
                "runId": run_id,
                "sessionKey": session_id,
                "position": result.get("position", 1),
                "queueDepth": result.get("queueDepth", 0),
                "acceptedAt": now_ms(),
            }
        # rejected (queue full): return explicit queue_full so client can distinguish from in_flight
        return {
            "status": "queue_full",
            "ok": False,
            "code": "QUEUE_FULL",
            "message": "Session queue is full, try again later",
            "sessionKey": session_id,
            "runId": run_id,
        }

    created = register_agent_job(run_id, session_key=session_id)
    if not created:
        # Session serialization: return current runId for followup (agent.wait or retry).
        existing_run_id = get_running_run_id_for_session(session_id) or run_id
        return {"runId": existing_run_id, "status": "in_flight", "sessionKey": session_id}

    async def _run_agent_job() -> None:
        from joyhousebot.services.chat.trace_context import trace_run_id, trace_session_key

        initial_delta = {
            "runId": run_id,
            "sessionKey": session_id,
            "state": "delta",
            "message": {"role": "assistant", "content": [{"type": "text", "text": ""}]},
        }
        if emit_event:
            await emit_event("chat", initial_delta)
        if broadcast_rpc_event:
            await broadcast_rpc_event("chat", initial_delta, None)
        token_run = trace_run_id.set(run_id)
        token_session = trace_session_key.set(session_id)
        try:
            payload = await chat(chat_message_cls(message=message, session_id=session_id, agent_id=agent_id))
            if payload.get("aborted"):
                complete_agent_job(run_id, status="aborted")
                if persist_trace:
                    persist_trace(run_id, session_id, "aborted", None)
                aborted_payload = {"runId": run_id, "sessionKey": session_id, "state": "aborted"}
                if emit_event:
                    await emit_event("chat", aborted_payload)
                if broadcast_rpc_event:
                    await broadcast_rpc_event("chat", aborted_payload, None)
                await fanout_chat_to_subscribed_nodes(
                    session_key=session_id,
                    payload={"runId": run_id, "sessionKey": session_id, "state": "aborted"},
                )
                return
            final_payload = {
                "runId": run_id,
                "sessionKey": session_id,
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": payload.get("response", "")}],
                },
            }
            complete_agent_job(run_id, status="ok", result=final_payload)
            if persist_trace:
                persist_trace(run_id, session_id, "ok", None)
            if emit_event:
                await emit_event("chat", final_payload)
            if broadcast_rpc_event:
                await broadcast_rpc_event("chat", final_payload, None)
            await fanout_chat_to_subscribed_nodes(session_key=session_id, payload=final_payload)
        except Exception as exc:
            error_payload = {"runId": run_id, "sessionKey": session_id, "state": "error", "error": str(exc)}
            complete_agent_job(run_id, status="error", error=str(exc), result=None)
            if persist_trace:
                persist_trace(run_id, session_id, "error", str(exc))
            if emit_event:
                await emit_event("chat", error_payload)
            if broadcast_rpc_event:
                await broadcast_rpc_event("chat", error_payload, None)
        finally:
            trace_run_id.reset(token_run)
            trace_session_key.reset(token_session)

    asyncio.create_task(_run_agent_job())
    if expect_final:
        snapshot = await wait_agent_job(run_id, timeout_ms=max(0, timeout_ms))
        if not snapshot:
            return {"runId": run_id, "status": "timeout"}
        out = {
            "runId": run_id,
            "status": snapshot.get("status"),
            "startedAt": snapshot.get("startedAt"),
            "endedAt": snapshot.get("endedAt"),
            "error": snapshot.get("error"),
            "sessionKey": session_id,
        }
        if snapshot.get("state"):
            out["state"] = snapshot["state"]
        if snapshot.get("message") is not None:
            out["message"] = snapshot["message"]
        return out
    ack_status = "started" if method == "chat.send" else "accepted"
    return {"status": ack_status, "ok": True, "runId": run_id, "sessionKey": session_id, "acceptedAt": now_ms()}


async def _agent_wait(
    *,
    params: dict[str, Any],
    wait_agent_job: Callable[[str], Awaitable[dict[str, Any] | None]] | Callable[..., Awaitable[dict[str, Any] | None]],
) -> dict[str, Any]:
    run_id = str(params.get("runId") or "").strip()
    if not run_id:
        raise ServiceError(code="INVALID_REQUEST", message="agent.wait requires runId")
    timeout_ms = int(params.get("timeoutMs") or 30000)
    snapshot = await wait_agent_job(run_id, timeout_ms=max(0, timeout_ms))
    if not snapshot:
        return {"runId": run_id, "status": "timeout"}
    return {
        "runId": run_id,
        "status": snapshot.get("status"),
        "startedAt": snapshot.get("startedAt"),
        "endedAt": snapshot.get("endedAt"),
        "error": snapshot.get("error"),
    }


def _chat_inject(
    *,
    params: dict[str, Any],
    resolve_agent: Callable[[Any], Any | None],
    now_iso: Callable[[], str],
) -> dict[str, Any]:
    """Append message to session only (collect semantics); does not start a new run. See docs/openclaw-implementation-verification.md command-queue semantics."""
    session_id = str(params.get("sessionKey") or params.get("sessionId") or params.get("session_id") or "main")
    role = str(params.get("role") or "user").strip() or "user"
    text = str(params.get("text") or params.get("message") or "").strip()
    if not text:
        raise ServiceError(code="INVALID_REQUEST", message="chat.inject requires text")
    agent = resolve_agent(params.get("agentId") or params.get("agent_id"))
    if not agent:
        raise ServiceError(code="UNAVAILABLE", message="agent not initialized")
    session = agent.sessions.get_or_create(session_id)
    session.messages.append({"role": role, "content": text, "timestamp": now_iso()})
    agent.sessions.save(session)
    return {"ok": True, "sessionKey": session_id}


async def _chat_abort(
    *,
    params: dict[str, Any],
    emit_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    request_abort: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    session_key = str(params.get("sessionKey") or "main")
    run_id = str(params.get("runId") or "")
    if request_abort and run_id:
        request_abort(run_id)
    if emit_event:
        await emit_event("chat", {"runId": run_id or uuid.uuid4().hex[:12], "sessionKey": session_key, "state": "aborted"})
    return {"ok": True, "aborted": True}


def _chat_history(
    *,
    params: dict[str, Any],
    resolve_agent: Callable[[Any], Any | None],
    build_chat_history_payload: Callable[[Any, int], dict[str, Any]],
) -> dict[str, Any]:
    session_key = str(params.get("sessionKey") or params.get("sessionId") or params.get("session_id") or "main")
    agent_id = params.get("agentId") or params.get("agent_id")
    agent = resolve_agent(agent_id)
    if not agent:
        raise ServiceError(code="UNAVAILABLE", message="agent not initialized")
    session = agent.sessions.get_or_create(session_key)
    limit = int(params.get("limit") or 200)
    return build_chat_history_payload(session, limit=max(1, min(limit, 1000)))


async def run_agent_job_with_params(
    *,
    item: dict[str, Any],
    chat: Callable[[Any], Awaitable[dict[str, Any]]],
    chat_message_cls: type,
    complete_agent_job: Callable[..., None],
    emit_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
    fanout_chat_to_subscribed_nodes: Callable[[str, dict[str, Any]], Awaitable[None]],
    broadcast_rpc_event: Callable[[str, Any, set[str] | None], Awaitable[None]] | None = None,
    persist_trace: Callable[[str, str, str, str | None], None] | None = None,
) -> None:
    """Run one agent job from a dequeued lane item (runId, sessionKey, params). Used after lane_dequeue_next."""
    run_id = str(item.get("runId", ""))
    session_id = str(item.get("sessionKey", "main"))
    params = item.get("params") or {}
    message = str(params.get("message") or params.get("text") or "").strip()
    agent_id = params.get("agentId") or params.get("agent_id")
    if not message:
        complete_agent_job(run_id, status="error", error="queued item missing message")
        return
    initial_delta = {
        "runId": run_id,
        "sessionKey": session_id,
        "state": "delta",
        "message": {"role": "assistant", "content": [{"type": "text", "text": ""}]},
    }
    if emit_event:
        await emit_event("chat", initial_delta)
    if broadcast_rpc_event:
        await broadcast_rpc_event("chat", initial_delta, None)
    from joyhousebot.services.chat.trace_context import trace_run_id, trace_session_key

    token_run = trace_run_id.set(run_id)
    token_session = trace_session_key.set(session_id)
    try:
        payload = await chat(chat_message_cls(message=message, session_id=session_id, agent_id=agent_id))
        final_payload = {
            "runId": run_id,
            "sessionKey": session_id,
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": payload.get("response", "")}],
            },
        }
        complete_agent_job(run_id, status="ok", result=final_payload)
        if persist_trace:
            persist_trace(run_id, session_id, "ok", None)
        if emit_event:
            await emit_event("chat", final_payload)
        if broadcast_rpc_event:
            await broadcast_rpc_event("chat", final_payload, None)
        await fanout_chat_to_subscribed_nodes(session_key=session_id, payload=final_payload)
    except Exception as exc:
        error_payload = {"runId": run_id, "sessionKey": session_id, "state": "error", "error": str(exc)}
        complete_agent_job(run_id, status="error", error=str(exc), result=None)
        if persist_trace:
            persist_trace(run_id, session_id, "error", str(exc))
        if emit_event:
            await emit_event("chat", error_payload)
        if broadcast_rpc_event:
            await broadcast_rpc_event("chat", error_payload, None)
    finally:
        trace_run_id.reset(token_run)
        trace_session_key.reset(token_session)

