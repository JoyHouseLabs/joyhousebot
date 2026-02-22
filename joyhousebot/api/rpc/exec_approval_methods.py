"""RPC handlers for exec approval workflows."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_exec_approval_method(
    *,
    method: str,
    params: dict[str, Any],
    app_state: dict[str, Any],
    client_id: str | None,
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    cleanup_expired_exec_approvals: Callable[[], None],
    now_ms: Callable[[], int],
    broadcast_rpc_event: Callable[[str, Any, set[str] | None], Awaitable[None]],
    load_persistent_state: Callable[[str, Any], Any],
    save_persistent_state: Callable[[str, Any], None],
) -> RpcResult | None:
    """Handle exec.approval.* and exec.approvals.* methods."""
    if method == "exec.approval.request":
        cleanup_expired_exec_approvals()
        request_id = str(params.get("id") or "").strip() or f"apr_{uuid.uuid4().hex[:12]}"
        timeout_ms = int(params.get("timeoutMs") or 300000)
        two_phase = bool(params.get("twoPhase", False))
        command = str(params.get("command") or "").strip()
        if not command:
            return False, None, rpc_error("INVALID_REQUEST", "command is required", None)
        pending: dict[str, Any] = app_state.get("rpc_exec_approval_pending") or {}
        futures: dict[str, asyncio.Future[Any]] = app_state.get("rpc_exec_approval_futures") or {}
        if request_id in pending and not pending[request_id].get("decision"):
            return False, None, rpc_error("INVALID_REQUEST", "approval id already pending", None)
        created_at = now_ms()
        expires_at = created_at + max(1, timeout_ms)
        req_obj = {
            "command": command,
            "cwd": params.get("cwd"),
            "host": params.get("host"),
            "security": params.get("security"),
            "ask": params.get("ask"),
            "agentId": params.get("agentId"),
            "resolvedPath": params.get("resolvedPath"),
            "sessionKey": params.get("sessionKey"),
        }
        pending[request_id] = {
            "id": request_id,
            "request": req_obj,
            "createdAtMs": created_at,
            "expiresAtMs": expires_at,
            "decision": None,
            "status": "pending",
            "requestedBy": client_id,
        }
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        futures[request_id] = fut
        app_state["rpc_exec_approval_pending"] = pending
        app_state["rpc_exec_approval_futures"] = futures
        requested_payload = {"id": request_id, "request": req_obj, "createdAtMs": created_at, "expiresAtMs": expires_at}
        await broadcast_rpc_event(
            "exec.approval.requested",
            requested_payload,
            {"operator"},
        )
        on_requested = app_state.get("on_exec_approval_requested")
        if callable(on_requested):
            try:
                await on_requested(requested_payload)
            except Exception:
                pass  # do not fail the request if forward fails
        if two_phase:
            return True, {"status": "accepted", "id": request_id, "createdAtMs": created_at, "expiresAtMs": expires_at}, None
        try:
            decision = await asyncio.wait_for(fut, timeout=max(1, timeout_ms) / 1000.0)
        except asyncio.TimeoutError:
            decision = None
            if request_id in pending:
                pending[request_id]["status"] = "expired"
                pending[request_id]["decision"] = None
        finally:
            futures.pop(request_id, None)
            app_state["rpc_exec_approval_pending"] = pending
            app_state["rpc_exec_approval_futures"] = futures
        return True, {"id": request_id, "decision": decision, "createdAtMs": created_at, "expiresAtMs": expires_at}, None

    if method == "exec.approval.waitDecision":
        cleanup_expired_exec_approvals()
        request_id = str(params.get("id") or "").strip()
        if not request_id:
            return False, None, rpc_error("INVALID_REQUEST", "id is required", None)
        pending: dict[str, Any] = app_state.get("rpc_exec_approval_pending") or {}
        futures: dict[str, asyncio.Future[Any]] = app_state.get("rpc_exec_approval_futures") or {}
        rec = pending.get(request_id)
        if not rec:
            return False, None, rpc_error("INVALID_REQUEST", "approval expired or not found", None)
        if rec.get("decision") in {"allow-once", "allow-always", "deny"}:
            return True, {"id": request_id, "decision": rec.get("decision"), "createdAtMs": rec.get("createdAtMs"), "expiresAtMs": rec.get("expiresAtMs")}, None
        fut = futures.get(request_id)
        if fut is None:
            fut = asyncio.get_running_loop().create_future()
            futures[request_id] = fut
            app_state["rpc_exec_approval_futures"] = futures
        timeout_left_ms = max(1, int(rec.get("expiresAtMs") or now_ms()) - now_ms())
        try:
            decision = await asyncio.wait_for(fut, timeout=max(1, timeout_left_ms) / 1000.0)
        except asyncio.TimeoutError:
            decision = None
        return True, {"id": request_id, "decision": decision, "createdAtMs": rec.get("createdAtMs"), "expiresAtMs": rec.get("expiresAtMs")}, None

    if method == "exec.approval.resolve":
        cleanup_expired_exec_approvals()
        request_id = str(params.get("requestId") or params.get("id") or "").strip()
        decision = str(params.get("decision") or "").strip().lower()
        if not request_id or decision not in {"allow-once", "allow-always", "deny"}:
            return False, None, rpc_error("INVALID_REQUEST", "requestId/id and decision(allow-once|allow-always|deny) required", None)
        pending: dict[str, Any] = app_state.get("rpc_exec_approval_pending") or {}
        futures: dict[str, asyncio.Future[Any]] = app_state.get("rpc_exec_approval_futures") or {}
        rec = pending.get(request_id)
        if not rec:
            return False, None, rpc_error("INVALID_REQUEST", "unknown approval id", None)
        rec["decision"] = decision
        rec["status"] = "resolved"
        rec["resolvedAtMs"] = now_ms()
        rec["resolvedBy"] = client_id
        fut = futures.get(request_id)
        if fut is not None and not fut.done():
            fut.set_result(decision)
        app_state["rpc_exec_approval_pending"] = pending
        app_state["rpc_exec_approval_futures"] = futures
        resolved_payload = {"id": request_id, "decision": decision, "resolvedBy": client_id, "ts": now_ms()}
        await broadcast_rpc_event(
            "exec.approval.resolved",
            resolved_payload,
            {"operator", "node"},
        )
        on_resolved = app_state.get("on_exec_approval_resolved")
        if callable(on_resolved):
            try:
                await on_resolved(resolved_payload)
            except Exception:
                pass
        return True, {"ok": True}, None

    if method == "exec.approvals.pending":
        pending: dict[str, Any] = app_state.get("rpc_exec_approval_pending") or {}
        now = now_ms()
        list_ = []
        for req_id, rec in pending.items():
            if rec.get("decision") or rec.get("status") == "resolved":
                continue
            if int(rec.get("expiresAtMs") or 0) < now:
                continue
            list_.append({
                "id": req_id,
                "request": rec.get("request"),
                "createdAtMs": rec.get("createdAtMs"),
                "expiresAtMs": rec.get("expiresAtMs"),
                "status": rec.get("status", "pending"),
            })
        return True, {"pending": list_}, None

    if method == "exec.approvals.get":
        data = load_persistent_state("rpc.exec_approvals", {"version": 1, "defaults": {}, "agents": {}})
        return True, {"path": "~/.joyhousebot/exec-approvals.json", "exists": True, "hash": "inline", "file": data}, None

    if method == "exec.approvals.set":
        file_data = params.get("file") if isinstance(params.get("file"), dict) else {}
        save_persistent_state("rpc.exec_approvals", file_data)
        return True, {"ok": True}, None

    if method == "exec.approvals.node.get":
        node_id = str(params.get("nodeId") or "")
        node_map = load_persistent_state("rpc.node_exec_approvals", {})
        data = node_map.get(node_id) or {"version": 1, "defaults": {}, "agents": {}}
        return True, {"path": f"node:{node_id}", "exists": True, "hash": "inline", "file": data}, None

    if method == "exec.approvals.node.set":
        node_id = str(params.get("nodeId") or "")
        file_data = params.get("file") if isinstance(params.get("file"), dict) else {}
        node_map = load_persistent_state("rpc.node_exec_approvals", {})
        node_map[node_id] = file_data
        save_persistent_state("rpc.node_exec_approvals", node_map)
        return True, {"ok": True}, None

    return None

