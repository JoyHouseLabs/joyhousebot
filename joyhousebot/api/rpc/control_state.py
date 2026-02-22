"""RPC handlers for control-plane lightweight state methods."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from joyhousebot.services.control.control_service import try_handle_control_state
from joyhousebot.services.errors import ServiceError


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_control_state_method(
    *,
    method: str,
    params: dict[str, Any],
    config: Any,
    app_state: dict[str, Any],
    emit_event: Callable[[str, Any], Awaitable[None]] | None,
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    load_persistent_state: Callable[[str, Any], Any],
    save_persistent_state: Callable[[str, Any], None],
    now_ms: Callable[[], int],
    build_skills_status_report: Callable[[Any], dict[str, Any]],
    build_channels_status_snapshot: Callable[[Any, Any], dict[str, Any]],
    get_cached_config: Callable[..., Any],
    save_config: Callable[[Any], None],
) -> RpcResult | None:
    """Handle state-like RPC methods. Return None when method is unrelated."""
    try:
        payload = await try_handle_control_state(
            method=method,
            params=params,
            config=config,
            app_state=app_state,
            emit_event=emit_event,
            load_persistent_state=load_persistent_state,
            save_persistent_state=save_persistent_state,
            now_ms=now_ms,
            build_skills_status_report=build_skills_status_report,
            build_channels_status_snapshot=build_channels_status_snapshot,
            get_cached_config=get_cached_config,
            save_config=save_config,
        )
        if payload is None:
            return None
        return True, payload, None
    except ServiceError as exc:
        return False, None, rpc_error(exc.code, exc.message, None)

