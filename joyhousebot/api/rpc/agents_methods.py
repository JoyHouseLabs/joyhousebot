"""RPC handlers for agents.* and agent identity methods."""

from __future__ import annotations

from typing import Any, Callable

from joyhousebot.services.agents.agent_service import (
    create_agent,
    delete_agent,
    get_agent_file,
    get_agent_identity,
    list_agent_files,
    list_agents,
    set_agent_file,
    update_agent,
)
from joyhousebot.services.errors import ServiceError

RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_agents_method(
    *,
    method: str,
    params: dict[str, Any],
    config: Any,
    app_state: dict[str, Any],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    build_agents_list_payload: Callable[[Any], dict[str, Any]],
    normalize_agent_id: Callable[[str], str],
    ensure_agent_workspace_bootstrap: Callable[[Path, str], None],
    now_ms: Callable[[], int],
    save_config: Callable[[Any], None],
    get_cached_config: Callable[..., Any],
) -> RpcResult | None:
    """Handle agents.* and agent.identity.get methods."""
    if method == "agents.list":
        return True, list_agents(config=config, build_agents_list_payload=build_agents_list_payload), None

    if method == "agents.create":
        try:
            payload = create_agent(
                params=params,
                config=config,
                app_state=app_state,
                normalize_agent_id=normalize_agent_id,
                ensure_agent_workspace_bootstrap=ensure_agent_workspace_bootstrap,
                save_config=save_config,
                get_cached_config=get_cached_config,
            )
            return True, payload, None
        except ServiceError as exc:
            return False, None, rpc_error(exc.code, exc.message, None)

    if method == "agents.update":
        try:
            payload = update_agent(
                params=params,
                config=config,
                app_state=app_state,
                normalize_agent_id=normalize_agent_id,
                ensure_agent_workspace_bootstrap=ensure_agent_workspace_bootstrap,
                save_config=save_config,
                get_cached_config=get_cached_config,
            )
            return True, payload, None
        except ServiceError as exc:
            return False, None, rpc_error(exc.code, exc.message, None)

    if method == "agents.delete":
        try:
            payload = delete_agent(
                params=params,
                config=config,
                app_state=app_state,
                normalize_agent_id=normalize_agent_id,
                save_config=save_config,
                get_cached_config=get_cached_config,
            )
            return True, payload, None
        except ServiceError as exc:
            return False, None, rpc_error(exc.code, exc.message, None)

    if method == "agents.files.list":
        try:
            return True, list_agent_files(params=params, config=config), None
        except ServiceError as exc:
            return False, None, rpc_error(exc.code, exc.message, None)

    if method == "agents.files.get":
        try:
            return True, get_agent_file(params=params, config=config), None
        except ServiceError as exc:
            return False, None, rpc_error(exc.code, exc.message, None)

    if method == "agents.files.set":
        try:
            return True, set_agent_file(params=params, config=config), None
        except ServiceError as exc:
            return False, None, rpc_error(exc.code, exc.message, None)

    if method == "agent.identity.get":
        return True, get_agent_identity(params=params, config=config), None

    return None

