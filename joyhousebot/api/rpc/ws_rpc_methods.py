"""Helpers for /ws/rpc runtime flow in API server."""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable


async def try_handle_rpc_presence_frame(
    *,
    frame: dict[str, Any],
    connection_key: str,
    presence_upsert: Callable[..., None],
    presence_entries: Callable[[], list[Any]],
    normalize_presence_entry: Callable[[Any], dict[str, Any]],
    emit_event: Callable[[str, Any], Awaitable[None]],
) -> bool:
    """Handle rpc websocket presence frame. Returns True when handled."""
    if frame.get("type") != "presence":
        return False
    instance_id = frame.get("instanceId") or connection_key
    presence_upsert(
        instance_id,
        reason="periodic",
        mode=frame.get("mode", "ui"),
        last_input_seconds=frame.get("lastInputSeconds"),
        host=frame.get("host"),
        version=frame.get("version"),
        connection_key=connection_key,
    )
    await emit_event("presence", {"presence": [normalize_presence_entry(e) for e in presence_entries()]})
    return True


def build_rpc_ws_response(
    *,
    frame: dict[str, Any],
    ok: bool,
    payload: Any,
    error: dict[str, Any] | None,
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
) -> dict[str, Any]:
    """Build response frame for rpc websocket request."""
    req_id = frame.get("id") if isinstance(frame, dict) else ""
    response: dict[str, Any] = {
        "type": "res",
        "id": req_id if isinstance(req_id, str) and req_id else f"invalid_{uuid.uuid4().hex[:8]}",
        "ok": ok,
    }
    if ok:
        response["payload"] = payload
    else:
        response["error"] = error or rpc_error("INTERNAL_ERROR", "unknown error", None)
    return response


async def handle_rpc_connect_postprocess(
    *,
    frame: dict[str, Any],
    ok: bool,
    client: Any,
    connection_key: str,
    client_host: str | None,
    websocket: Any,
    app_state: dict[str, Any],
    node_session_cls: type,
    node_registry_cls: type,
    now_ms: Callable[[], int],
) -> None:
    """Handle post-connect node registration and rpc connection metadata update."""
    method = frame.get("method") if isinstance(frame, dict) else None
    if not isinstance(method, str):
        return
    if method == "connect" and ok and client.role == "node":
        params = frame.get("params") if isinstance(frame.get("params"), dict) else {}
        node_id = str(params.get("nodeId") or client.client_id or connection_key).strip()
        node = node_session_cls(
            node_id=node_id,
            conn_id=connection_key,
            display_name=str(params.get("displayName") or "") or None,
            platform=str(params.get("platform") or "") or None,
            version=str(params.get("version") or "") or None,
            core_version=str(params.get("coreVersion") or "") or None,
            ui_version=str(params.get("uiVersion") or "") or None,
            device_family=str(params.get("deviceFamily") or "") or None,
            model_identifier=str(params.get("modelIdentifier") or "") or None,
            remote_ip=client_host,
            caps=[str(x) for x in (params.get("caps") or []) if str(x).strip()],
            commands=[str(x) for x in (params.get("commands") or []) if str(x).strip()],
            permissions=params.get("permissions") if isinstance(params.get("permissions"), dict) else None,
            path_env=str(params.get("pathEnv") or "") or None,
            connected_at_ms=now_ms(),
        )
        node_registry = app_state.get("node_registry") or node_registry_cls()
        app_state["node_registry"] = node_registry
        await node_registry.register(node=node, socket=websocket)
    if method == "connect" and ok:
        rpc_connections = app_state.get("rpc_connections") or {}
        scopes = getattr(client, "scopes", None)
        rpc_connections[connection_key] = {
            "websocket": websocket,
            "role": client.role,
            "clientId": client.client_id or connection_key,
            "scopes": list(scopes) if scopes is not None else [],
        }
        app_state["rpc_connections"] = rpc_connections


async def cleanup_rpc_ws_connection(
    *,
    connection_key: str,
    app_state: dict[str, Any],
    node_registry_cls: type,
    presence_remove_by_connection: Callable[[str], None],
) -> None:
    """Cleanup rpc websocket connection state on disconnect/error."""
    node_registry = app_state.get("node_registry") or node_registry_cls()
    await node_registry.unregister_by_conn(connection_key)
    rpc_connections = app_state.get("rpc_connections") or {}
    rpc_connections.pop(connection_key, None)
    app_state["rpc_connections"] = rpc_connections
    nonces = app_state.get("rpc_connect_nonces")
    if isinstance(nonces, dict):
        nonces.pop(connection_key, None)
    presence_remove_by_connection(connection_key)


async def run_rpc_ws_loop(
    *,
    websocket: Any,
    connection_key: str,
    client_host: str | None,
    client: Any,
    app_state: dict[str, Any],
    emit_event: Callable[[str, Any], Awaitable[None]],
    handle_rpc_request: Callable[[dict[str, Any], Any, str, Callable[[str, Any], Awaitable[None]]], Awaitable[tuple[bool, Any, dict[str, Any] | None]]],
    presence_upsert: Callable[..., None],
    presence_entries: Callable[[], list[Any]],
    normalize_presence_entry: Callable[[Any], dict[str, Any]],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    logger_info: Callable[[str, Any, Any, Any], None],
    handle_connect_postprocess: Callable[..., Awaitable[None]],
    node_session_cls: type,
    node_registry_cls: type,
    now_ms: Callable[[], int],
) -> None:
    """Run /ws/rpc frame processing loop."""
    while True:
        frame = await websocket.receive_json()
        if await try_handle_rpc_presence_frame(
            frame=frame,
            connection_key=connection_key,
            presence_upsert=presence_upsert,
            presence_entries=presence_entries,
            normalize_presence_entry=normalize_presence_entry,
            emit_event=emit_event,
        ):
            continue
        ok, payload, error = await handle_rpc_request(frame, client, connection_key, emit_event, client_host)
        response = build_rpc_ws_response(
            frame=frame,
            ok=ok,
            payload=payload,
            error=error,
            rpc_error=rpc_error,
        )
        method = frame.get("method") if isinstance(frame, dict) else None
        if isinstance(method, str):
            logger_info("RPC request method={} ok={} client={}", method, ok, client.client_id or connection_key)
            await handle_connect_postprocess(
                frame=frame,
                ok=ok,
                client=client,
                connection_key=connection_key,
                client_host=client_host,
                websocket=websocket,
                app_state=app_state,
                node_session_cls=node_session_cls,
                node_registry_cls=node_registry_cls,
                now_ms=now_ms,
            )
        await websocket.send_json(response)

