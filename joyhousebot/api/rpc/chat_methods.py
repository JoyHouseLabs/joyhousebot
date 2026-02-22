"""RPC handlers for chat/agent runtime methods."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from joyhousebot.services.chat.chat_service import try_handle_chat_runtime
from joyhousebot.services.errors import ServiceError


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_chat_runtime_method(
    *,
    method: str,
    params: dict[str, Any],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
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
    lane_can_run: Callable[[str], bool] | None = None,
    lane_enqueue: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
    persist_trace: Callable[[str, str, str, str | None], None] | None = None,
    request_abort: Callable[[str], None] | None = None,
) -> RpcResult | None:
    """Handle chat and agent runtime RPC methods."""
    try:
        payload = await try_handle_chat_runtime(
            method=method,
            params=params,
            register_agent_job=register_agent_job,
            get_running_run_id_for_session=get_running_run_id_for_session,
            complete_agent_job=complete_agent_job,
            wait_agent_job=wait_agent_job,
            chat=chat,
            chat_message_cls=chat_message_cls,
            resolve_agent=resolve_agent,
            build_chat_history_payload=build_chat_history_payload,
            now_iso=now_iso,
            now_ms=now_ms,
            emit_event=emit_event,
            fanout_chat_to_subscribed_nodes=fanout_chat_to_subscribed_nodes,
            lane_can_run=lane_can_run,
            lane_enqueue=lane_enqueue,
            persist_trace=persist_trace,
            request_abort=request_abort,
        )
        if payload is None:
            return None
        return True, payload, None
    except ServiceError as exc:
        return False, None, rpc_error(exc.code, exc.message, None)

