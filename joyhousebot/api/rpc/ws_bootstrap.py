"""Bootstrap helpers for websocket endpoints in API server."""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable


async def bootstrap_rpc_ws_connection(
    *,
    websocket: Any,
    app_state: dict[str, Any],
    presence_upsert: Callable[..., None],
    client_state_cls: type,
) -> tuple[str, str | None, Any, Callable[[str, Any], Awaitable[None]]]:
    """Accept and initialize /ws/rpc connection state and event sender.
    Sends connect.challenge with nonce so clients can sign device payload.
    """
    await websocket.accept()
    connection_key = f"rpc_{uuid.uuid4().hex[:12]}"
    client_host = getattr(websocket.client, "host", None) if websocket.client else None
    presence_upsert(
        connection_key,
        reason="connect",
        mode="ui",
        ip=client_host,
        connection_key=connection_key,
    )
    client = client_state_cls()
    seq_state = {"seq": 0}

    connect_nonce = f"n_{uuid.uuid4().hex[:16]}"
    nonces = app_state.get("rpc_connect_nonces")
    if nonces is None:
        nonces = {}
        app_state["rpc_connect_nonces"] = nonces
    nonces[connection_key] = connect_nonce

    async def emit_event(event: str, payload: Any) -> None:
        seq_state["seq"] += 1
        seq = seq_state["seq"]
        await websocket.send_json(
            {
                "type": "event",
                "event": event,
                "payload": payload,
                "seq": seq,
                "stateVersion": {"presence": seq, "health": seq},
            }
        )

    await emit_event("connect.challenge", {"nonce": connect_nonce})

    rpc_connections = app_state.get("rpc_connections") or {}
    rpc_connections[connection_key] = {"websocket": websocket, "role": "unknown", "clientId": connection_key}
    app_state["rpc_connections"] = rpc_connections
    return connection_key, client_host, client, emit_event


async def bootstrap_chat_ws_connection(
    *,
    websocket: Any,
    manager_connect: Callable[[Any], Awaitable[None]],
    ws_to_presence_key: dict[Any, str],
    presence_upsert: Callable[..., None],
) -> str:
    """Accept and initialize /ws/chat connection state."""
    await manager_connect(websocket)
    connection_key = f"ws_{uuid.uuid4().hex[:12]}"
    client_host = getattr(websocket.client, "host", None) if websocket.client else None
    presence_upsert(
        connection_key,
        reason="connect",
        mode="webchat",
        ip=client_host,
        connection_key=connection_key,
    )
    ws_to_presence_key[websocket] = connection_key
    return connection_key

