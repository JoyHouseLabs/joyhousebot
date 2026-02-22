"""WebSocket endpoint error/cleanup helpers."""

from __future__ import annotations

from typing import Any, Callable


async def handle_rpc_ws_close(
    *,
    connection_key: str,
    app_state: dict[str, Any],
    node_registry_cls: type,
    presence_remove_by_connection: Callable[[str], None],
    logger_error: Callable[[str, Any], None] | None = None,
    exc: Exception | None = None,
) -> None:
    """Cleanup RPC websocket connection on disconnect/error."""
    if exc is not None and logger_error is not None:
        logger_error("RPC WebSocket error: {}", exc)

    from joyhousebot.api.rpc.ws_rpc_methods import cleanup_rpc_ws_connection

    await cleanup_rpc_ws_connection(
        connection_key=connection_key,
        app_state=app_state,
        node_registry_cls=node_registry_cls,
        presence_remove_by_connection=presence_remove_by_connection,
    )


def handle_chat_ws_close(
    *,
    websocket: Any,
    ws_to_presence_key: dict[int, str],
    presence_remove_by_connection: Callable[[str], None],
    manager_disconnect: Callable[[Any], None],
    logger_error: Callable[[str], None] | None = None,
    exc: Exception | None = None,
) -> None:
    """Cleanup chat websocket connection on disconnect/error."""
    if exc is not None and logger_error is not None:
        logger_error(f"WebSocket error: {exc}")

    from joyhousebot.api.rpc.ws_chat_methods import cleanup_chat_presence_and_connection

    cleanup_chat_presence_and_connection(
        websocket=websocket,
        ws_to_presence_key=ws_to_presence_key,
        presence_remove_by_connection=presence_remove_by_connection,
        manager_disconnect=manager_disconnect,
    )

