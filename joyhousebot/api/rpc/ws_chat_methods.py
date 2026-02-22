"""Helpers for /ws/chat runtime flow in API server."""

from __future__ import annotations

import time
from typing import Any, Callable


def try_handle_chat_presence_frame(
    *,
    data: dict[str, Any],
    connection_key: str,
    presence_upsert: Callable[..., None],
) -> bool:
    """Handle /ws/chat presence heartbeat frame. Returns True when handled."""
    if data.get("type") != "presence":
        return False
    instance_id = data.get("instanceId") or connection_key
    presence_upsert(
        instance_id,
        reason="periodic",
        mode=data.get("mode", "webchat"),
        last_input_seconds=data.get("lastInputSeconds"),
        host=data.get("host"),
        version=data.get("version"),
        connection_key=connection_key,
    )
    return True


async def process_websocket_chat_message(
    *,
    data: dict[str, Any],
    resolve_agent: Callable[[Any], Any | None],
    websocket: Any,
    logger_error: Callable[[str], None],
) -> None:
    """Process /ws/chat message payload and send response/error frames."""
    message = data.get("message", "")
    session_id = data.get("session_id", "ws:default")
    agent_id = data.get("agent_id")
    agent = resolve_agent(agent_id)

    if not message or not agent:
        return

    try:
        response = await agent.process_direct(
            content=message,
            session_key=session_id,
            channel="api",
            chat_id="websocket",
        )
        await websocket.send_json(
            {
                "type": "response",
                "response": response,
                "session_id": session_id,
                "timestamp": time.time(),
            }
        )
    except Exception as exc:
        logger_error(f"WebSocket chat error: {exc}")
        await websocket.send_json(
            {
                "type": "error",
                "error": str(exc),
                "timestamp": time.time(),
            }
        )


def cleanup_chat_presence_and_connection(
    *,
    websocket: Any,
    ws_to_presence_key: dict[Any, str],
    presence_remove_by_connection: Callable[[str], None],
    manager_disconnect: Callable[[Any], None],
) -> None:
    """Cleanup ws/chat presence and websocket manager state."""
    key = ws_to_presence_key.pop(websocket, None)
    if key:
        presence_remove_by_connection(key)
    manager_disconnect(websocket)


async def run_chat_ws_loop(
    *,
    websocket: Any,
    connection_key: str,
    presence_upsert: Callable[..., None],
    resolve_agent: Callable[[Any], Any | None],
    logger_error: Callable[[str], None],
) -> None:
    """Run /ws/chat frame processing loop."""
    while True:
        data = await websocket.receive_json()
        if try_handle_chat_presence_frame(
            data=data,
            connection_key=connection_key,
            presence_upsert=presence_upsert,
        ):
            continue
        await process_websocket_chat_message(
            data=data,
            resolve_agent=resolve_agent,
            websocket=websocket,
            logger_error=logger_error,
        )

