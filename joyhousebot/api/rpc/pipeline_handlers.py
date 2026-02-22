"""Composed RPC handlers used by server dispatch pipeline."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from joyhousebot.api.rpc.agents_methods import try_handle_agents_method
from joyhousebot.api.rpc.config_methods import try_handle_config_method
from joyhousebot.api.rpc.post_hooks import apply_shadow_hook_if_needed
from joyhousebot.api.rpc.sessions_usage import try_handle_sessions_usage_method


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def handle_agents_with_shadow(
    *,
    method: str,
    params: dict[str, Any],
    config: Any,
    app_state: dict[str, Any],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    build_agents_list_payload: Callable[[Any, Any], dict[str, Any]],
    normalize_agent_id: Callable[[Any], str],
    ensure_agent_workspace_bootstrap: Callable[[str], None],
    now_ms: Callable[[], int],
    save_config: Callable[[Any], None],
    get_cached_config: Callable[..., Any],
    run_rpc_shadow: Callable[[str, dict[str, Any], Any], Awaitable[None]],
) -> RpcResult | None:
    """Run agents handler and apply shadow hook for agents.list."""
    result = await try_handle_agents_method(
        method=method,
        params=params,
        config=config,
        app_state=app_state,
        rpc_error=rpc_error,
        build_agents_list_payload=build_agents_list_payload,
        normalize_agent_id=normalize_agent_id,
        ensure_agent_workspace_bootstrap=ensure_agent_workspace_bootstrap,
        now_ms=now_ms,
        save_config=save_config,
        get_cached_config=get_cached_config,
    )
    if result is None:
        return None
    return await apply_shadow_hook_if_needed(
        method=method,
        params=params,
        result=result,
        shadow_methods={"agents.list"},
        run_rpc_shadow=run_rpc_shadow,
    )


async def handle_sessions_usage_with_shadow(
    *,
    method: str,
    params: dict[str, Any],
    config: Any,
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    resolve_agent: Callable[[Any], Any | None],
    build_sessions_list_payload: Callable[[Any, Any], dict[str, Any]],
    build_chat_history_payload: Callable[[Any, int], dict[str, Any]],
    apply_session_patch: Callable[[Any, Any], tuple[bool, str | None]],
    now_ms: Callable[[], int],
    delete_session: Callable[[str, Any], None],
    empty_usage_totals: Callable[[], dict[str, Any]],
    session_usage_entry: Callable[..., dict[str, Any]],
    estimate_tokens: Callable[[str], int],
    run_rpc_shadow: Callable[[str, dict[str, Any], Any], Awaitable[None]],
) -> RpcResult | None:
    """Run sessions/usage handler and apply shadow hook for sessions.list."""
    result = await try_handle_sessions_usage_method(
        method=method,
        params=params,
        rpc_error=rpc_error,
        resolve_agent=resolve_agent,
        build_sessions_list_payload=build_sessions_list_payload,
        build_chat_history_payload=build_chat_history_payload,
        apply_session_patch=apply_session_patch,
        now_ms=now_ms,
        delete_session=delete_session,
        config=config,
        empty_usage_totals=empty_usage_totals,
        session_usage_entry=session_usage_entry,
        estimate_tokens=estimate_tokens,
    )
    if result is None:
        return None
    return await apply_shadow_hook_if_needed(
        method=method,
        params=params,
        result=result,
        shadow_methods={"sessions.list"},
        run_rpc_shadow=run_rpc_shadow,
    )


async def handle_config_with_shadow(
    *,
    method: str,
    params: dict[str, Any],
    config: Any,
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    build_config_snapshot: Callable[[Any], dict[str, Any]],
    build_config_schema_payload: Callable[[], dict[str, Any]],
    apply_config_from_raw: Callable[[dict[str, Any], Any], None],
    get_cached_config: Callable[..., Any],
    update_config: Callable[[Any], Any],
    config_update_cls: type,
    run_rpc_shadow: Callable[[str, dict[str, Any], Any], Awaitable[None]],
) -> RpcResult | None:
    """Run config handler and apply shadow hook for config.get."""
    result = await try_handle_config_method(
        method=method,
        params=params,
        rpc_error=rpc_error,
        build_config_snapshot=build_config_snapshot,
        build_config_schema_payload=build_config_schema_payload,
        apply_config_from_raw=apply_config_from_raw,
        get_cached_config=get_cached_config,
        update_config=update_config,
        config_update_cls=config_update_cls,
        config=config,
    )
    if result is None:
        return None
    return await apply_shadow_hook_if_needed(
        method=method,
        params=params,
        result=result,
        shadow_methods={"config.get"},
        run_rpc_shadow=run_rpc_shadow,
    )

