"""RPC handlers for node runtime methods."""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


def _build_node_list_payload(*, pairs: dict[str, Any], node_registry: Any, now_ms: Callable[[], int]) -> dict[str, Any]:
    paired_nodes = {
        str(row.get("deviceId")): row
        for row in pairs["paired"]
        if str(row.get("deviceId") or "").strip()
        and (
            str(row.get("role") or "").strip() == "node"
            or "node" in (row.get("roles") or [])
        )
    }
    live_nodes = {node.node_id: node for node in node_registry.list_connected()}
    node_ids = sorted(set(paired_nodes.keys()) | set(live_nodes.keys()))
    nodes = []
    for node_id in node_ids:
        paired = paired_nodes.get(node_id) or {}
        live = live_nodes.get(node_id)
        nodes.append(
            {
                "nodeId": node_id,
                "displayName": (live.display_name if live else None) or paired.get("displayName"),
                "platform": (live.platform if live else None) or paired.get("platform"),
                "version": (live.version if live else None) or paired.get("version"),
                "coreVersion": (live.core_version if live else None) or paired.get("coreVersion"),
                "uiVersion": (live.ui_version if live else None) or paired.get("uiVersion"),
                "deviceFamily": (live.device_family if live else None) or paired.get("deviceFamily"),
                "modelIdentifier": (live.model_identifier if live else None) or paired.get("modelIdentifier"),
                "remoteIp": (live.remote_ip if live else None) or paired.get("remoteIp"),
                "caps": sorted(set(list(live.caps) if live else []) | set(list(paired.get("caps") or []))),
                "commands": sorted(set(list(live.commands) if live else []) | set(list(paired.get("commands") or []))),
                "permissions": (live.permissions if live else None) or paired.get("permissions"),
                "pathEnv": (live.path_env if live else None) or paired.get("pathEnv"),
                "connectedAtMs": live.connected_at_ms if live else None,
                "paired": bool(paired),
                "connected": bool(live),
            }
        )
    return {"ts": now_ms(), "nodes": nodes}


async def try_handle_node_runtime_method(
    *,
    method: str,
    params: dict[str, Any],
    client_id: str | None,
    app_state: dict[str, Any],
    node_registry: Any,
    config: Any,
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    load_device_pairs_state: Callable[[], dict[str, Any]],
    save_persistent_state: Callable[[str, Any], None],
    now_ms: Callable[[], int],
    resolve_node_command_allowlist: Callable[[Any, Any], list[str] | None],
    is_node_command_allowed: Callable[[str, list[str], list[str] | None], tuple[bool, str]],
    normalize_node_event_payload: Callable[[dict[str, Any]], tuple[Any, str | None]],
    run_node_agent_request: Callable[..., Awaitable[tuple[bool, str]]],
    get_store: Callable[[], Any],
    broadcast_rpc_event: Callable[[str, Any, set[str] | None], Awaitable[None]],
) -> RpcResult | None:
    """Handle node runtime operations. Return None when method is unrelated."""
    if method == "node.rename":
        node_id = str(params.get("nodeId") or "").strip()
        display_name = str(params.get("displayName") or "").strip()
        if not node_id or not display_name:
            return False, None, rpc_error("INVALID_REQUEST", "nodeId and displayName required", None)
        pairs = load_device_pairs_state()
        updated = False
        for row in pairs["paired"]:
            if str(row.get("deviceId")) == node_id:
                row["displayName"] = display_name
                updated = True
        save_persistent_state("rpc.device_pairs", pairs)
        if not updated:
            return False, None, rpc_error("INVALID_REQUEST", "unknown nodeId", None)
        return True, {"nodeId": node_id, "displayName": display_name}, None

    if method == "node.list":
        pairs = load_device_pairs_state()
        return True, _build_node_list_payload(pairs=pairs, node_registry=node_registry, now_ms=now_ms), None

    if method == "node.describe":
        node_id = str(params.get("nodeId") or "").strip()
        if not node_id:
            return False, None, rpc_error("INVALID_REQUEST", "nodeId required", None)
        pairs = load_device_pairs_state()
        payload = _build_node_list_payload(pairs=pairs, node_registry=node_registry, now_ms=now_ms)
        for node in payload.get("nodes", []):
            if str(node.get("nodeId")) == node_id:
                return True, {"ts": now_ms(), **node}, None
        return False, None, rpc_error("INVALID_REQUEST", "unknown nodeId", None)

    if method == "node.invoke":
        node_id = str(params.get("nodeId") or "").strip()
        command = str(params.get("command") or "").strip()
        if not node_id or not command:
            return False, None, rpc_error("INVALID_REQUEST", "nodeId and command required", None)
        if command in {"system.execApprovals.get", "system.execApprovals.set"}:
            return (
                False,
                None,
                rpc_error(
                    "INVALID_REQUEST",
                    "node.invoke does not allow system.execApprovals.*; use exec.approvals.node.*",
                    {"details": {"command": command}},
                ),
            )
        node_session = node_registry.get(node_id)
        if not node_session:
            return False, None, rpc_error("UNAVAILABLE", "node not connected", {"details": {"code": "NOT_CONNECTED"}})
        allowlist = resolve_node_command_allowlist(config, node_session)
        allowed, reason = is_node_command_allowed(command, node_session.commands, allowlist)
        if not allowed:
            return (
                False,
                None,
                rpc_error("INVALID_REQUEST", "node command not allowed", {"details": {"reason": reason, "command": command}}),
            )
        timeout_ms = int(params.get("timeoutMs") or 30000)
        result = await node_registry.invoke(
            node_id=node_id,
            command=command,
            params=params.get("params"),
            timeout_ms=max(100, timeout_ms),
            idempotency_key=str(params.get("idempotencyKey") or "") or None,
        )
        if not result.ok:
            err = result.error or {"code": "UNAVAILABLE", "message": "node invoke failed"}
            return (
                False,
                None,
                rpc_error(
                    str(err.get("code") or "UNAVAILABLE"),
                    str(err.get("message") or "node invoke failed"),
                    {"details": {"nodeId": node_id, "command": command}},
                ),
            )
        return (
            True,
            {
                "ok": True,
                "nodeId": node_id,
                "command": command,
                "payload": result.payload,
                "payloadJSON": result.payload_json,
            },
            None,
        )

    if method == "node.invoke.result":
        invoke_id = str(params.get("id") or "").strip()
        node_id = str(params.get("nodeId") or client_id or "").strip()
        if not invoke_id or not node_id:
            return False, None, rpc_error("INVALID_REQUEST", "id and nodeId required", None)
        accepted = node_registry.handle_invoke_result(
            invoke_id=invoke_id,
            node_id=node_id,
            ok=bool(params.get("ok", False)),
            payload=params.get("payload"),
            payload_json=params.get("payloadJSON") if isinstance(params.get("payloadJSON"), str) else None,
            error=params.get("error") if isinstance(params.get("error"), dict) else None,
        )
        return True, {"ok": True, "accepted": accepted}, None

    if method == "node.event":
        event_name = str(params.get("event") or "").strip()
        node_id = str(params.get("nodeId") or client_id or "").strip()
        if not event_name:
            return False, None, rpc_error("INVALID_REQUEST", "event required", None)
        payload_value, payload_json = normalize_node_event_payload(params)
        subscriptions: dict[str, set[str]] = app_state.get("rpc_node_subscriptions") or {}
        if event_name in {"voice.transcript", "agent.request"}:
            ok_req, req_err = await run_node_agent_request(
                node_id=node_id,
                payload_value=payload_value,
            )
            if not ok_req:
                return False, None, rpc_error("INVALID_REQUEST", req_err, None)
        elif event_name == "chat.subscribe":
            if isinstance(payload_value, dict):
                session_key = str(payload_value.get("sessionKey") or "").strip()
                if session_key:
                    subs = subscriptions.get(node_id) or set()
                    subs.add(session_key)
                    subscriptions[node_id] = subs
                    app_state["rpc_node_subscriptions"] = subscriptions
        elif event_name == "chat.unsubscribe":
            if isinstance(payload_value, dict):
                session_key = str(payload_value.get("sessionKey") or "").strip()
                if session_key:
                    subs = subscriptions.get(node_id) or set()
                    subs.discard(session_key)
                    if subs:
                        subscriptions[node_id] = subs
                    else:
                        subscriptions.pop(node_id, None)
                    app_state["rpc_node_subscriptions"] = subscriptions
        elif event_name in {"exec.started", "exec.finished", "exec.denied"}:
            ts = now_ms()
            app_state["rpc_last_heartbeat"] = ts
            save_persistent_state("rpc.last_heartbeat", {"ts": ts})
            if isinstance(payload_value, dict):
                run_id = str(payload_value.get("runId") or "").strip()
                task_id = run_id or f"node-exec-{uuid.uuid4().hex[:10]}"
                detail = {
                    "nodeId": node_id,
                    "event": event_name,
                    "command": payload_value.get("command"),
                    "exitCode": payload_value.get("exitCode"),
                    "timedOut": payload_value.get("timedOut"),
                    "reason": payload_value.get("reason"),
                }
                try:
                    get_store().log_task_event(task_id=task_id, event=event_name, detail=detail)
                except Exception:
                    pass
        raw_payload = {"nodeId": node_id, "event": event_name, "payload": payload_value, "payloadJSON": payload_json, "ts": now_ms()}
        forwarded_payload = {"nodeId": node_id, "payload": payload_value, "payloadJSON": payload_json, "ts": now_ms()}
        await broadcast_rpc_event(event_name, forwarded_payload, {"operator"})
        await broadcast_rpc_event("node.event", raw_payload, {"operator"})
        return True, {"ok": True, "nodeId": node_id, "event": event_name}, None

    return None

