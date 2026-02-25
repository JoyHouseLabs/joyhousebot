"""RPC handlers for device/node pairing workflows."""

from __future__ import annotations

import hmac
import uuid
from typing import Any, Awaitable, Callable


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_pairing_method(
    *,
    method: str,
    params: dict[str, Any],
    client_id: str | None,
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    load_persistent_state: Callable[[str, Any], Any],
    save_persistent_state: Callable[[str, Any], None],
    load_device_pairs_state: Callable[[], dict[str, Any]],
    hash_pairing_token: Callable[[str], str],
    now_ms: Callable[[], int],
    broadcast_rpc_event: Callable[[str, Any, set[str] | None], Awaitable[None]],
) -> RpcResult | None:
    """Handle device/node pairing methods. Return None when method is unrelated."""
    if method == "device.pair.list":
        pairs = load_persistent_state("rpc.device_pairs", {"pending": [], "paired": []})
        return True, {"pending": pairs.get("pending", []), "paired": pairs.get("paired", [])}, None

    if method == "device.pair.approve":
        request_id = str(params.get("requestId") or "")
        pairs = load_persistent_state("rpc.device_pairs", {"pending": [], "paired": []})
        pending = pairs.get("pending", [])
        paired = pairs.get("paired", [])
        match = None
        left = []
        for row in pending:
            if str(row.get("requestId")) == request_id:
                match = row
            else:
                left.append(row)
        if match:
            device_id = str(match.get("deviceId") or "").strip()
            scopes = list(match.get("scopes") or ["operator.read", "operator.write", "operator.admin"])
            if not isinstance(scopes, list):
                scopes = ["operator.read", "operator.write", "operator.admin"]
            scopes = [str(s).strip() for s in scopes if str(s).strip()]
            if not scopes:
                scopes = ["operator.read", "operator.write", "operator.admin"]
            token = f"tok_{uuid.uuid4().hex}"
            token_hash = hash_pairing_token(token)
            now = now_ms()
            paired.append(
                {
                    "deviceId": device_id,
                    "displayName": match.get("displayName"),
                    "roles": ["operator"],
                    "scopes": scopes,
                    "approvedAtMs": now,
                    "tokens": {
                        "operator": {
                            "tokenHash": token_hash,
                            "scopes": scopes,
                            "createdAtMs": now,
                            "lastUsedAtMs": None,
                            "revokedAtMs": None,
                        },
                    },
                }
            )
            save_persistent_state("rpc.device_pairs", {"pending": left, "paired": paired})
            return True, {"ok": True, "token": token, "deviceId": device_id, "role": "operator", "scopes": scopes}, None
        save_persistent_state("rpc.device_pairs", {"pending": left, "paired": paired})
        return True, {"ok": True}, None

    if method == "device.pair.reject":
        request_id = str(params.get("requestId") or "")
        pairs = load_persistent_state("rpc.device_pairs", {"pending": [], "paired": []})
        pending = [row for row in pairs.get("pending", []) if str(row.get("requestId")) != request_id]
        save_persistent_state("rpc.device_pairs", {"pending": pending, "paired": pairs.get("paired", [])})
        return True, {"ok": True}, None

    if method == "device.token.rotate":
        device_id = str(params.get("deviceId") or "").strip()
        role = str(params.get("role") or "operator").strip() or "operator"
        scopes_param = params.get("scopes")
        scopes = [str(s).strip() for s in scopes_param] if isinstance(scopes_param, list) else None
        pairs = load_persistent_state("rpc.device_pairs", {"pending": [], "paired": []})
        paired = pairs.get("paired", [])
        now = now_ms()
        for entry in paired:
            if str(entry.get("deviceId") or "").strip() != device_id:
                continue
            tokens = dict(entry.get("tokens") or {})
            existing = tokens.get(role)
            if isinstance(existing, dict):
                scopes = scopes or existing.get("scopes") or []
            else:
                scopes = scopes or ["operator.read", "operator.write", "operator.admin"]
            token = f"tok_{uuid.uuid4().hex}"
            token_hash = hash_pairing_token(token)
            tokens[role] = {
                "tokenHash": token_hash,
                "scopes": scopes,
                "createdAtMs": existing.get("createdAtMs", now) if isinstance(existing, dict) else now,
                "rotatedAtMs": now,
                "lastUsedAtMs": existing.get("lastUsedAtMs") if isinstance(existing, dict) else None,
                "revokedAtMs": None,
            }
            entry["tokens"] = tokens
            save_persistent_state("rpc.device_pairs", {"pending": pairs.get("pending", []), "paired": paired})
            return True, {"token": token, "deviceId": device_id, "role": role, "scopes": scopes}, None
        return False, None, rpc_error("INVALID_REQUEST", "device not paired", None)

    if method == "device.pair.remove":
        device_id = str(params.get("deviceId") or "").strip()
        if not device_id:
            return False, None, rpc_error("INVALID_REQUEST", "deviceId required", None)
        pairs = load_persistent_state("rpc.device_pairs", {"pending": [], "paired": []})
        pending = [row for row in pairs.get("pending", []) if str(row.get("deviceId")) != device_id]
        paired = [row for row in pairs.get("paired", []) if str(row.get("deviceId")) != device_id]
        save_persistent_state("rpc.device_pairs", {"pending": pending, "paired": paired})
        return True, {"ok": True, "deviceId": device_id}, None

    if method == "device.token.revoke":
        device_id = str(params.get("deviceId") or "").strip()
        role = str(params.get("role") or "operator").strip() or "operator"
        pairs = load_persistent_state("rpc.device_pairs", {"pending": [], "paired": []})
        paired = pairs.get("paired", [])
        now = now_ms()
        for entry in paired:
            if str(entry.get("deviceId") or "").strip() != device_id:
                continue
            tokens = dict(entry.get("tokens") or {})
            existing = tokens.get(role)
            if isinstance(existing, dict):
                existing = dict(existing)
                existing["revokedAtMs"] = now
                tokens[role] = existing
                entry["tokens"] = tokens
                save_persistent_state("rpc.device_pairs", {"pending": pairs.get("pending", []), "paired": paired})
            return True, {"ok": True}, None
        return True, {"ok": True}, None

    if method == "node.pair.request":
        node_id = str(params.get("nodeId") or client_id or "").strip()
        if not node_id:
            return False, None, rpc_error("INVALID_REQUEST", "nodeId required", None)
        pairs = load_device_pairs_state()
        pending = pairs["pending"]
        paired = pairs["paired"]
        if any(str(row.get("deviceId")) == node_id for row in paired):
            return True, {"status": "paired", "created": False}, None
        existing = next((row for row in pending if str(row.get("deviceId")) == node_id), None)
        if existing:
            return True, {"status": "pending", "created": False, "request": existing}, None
        request = {
            "requestId": f"npr_{uuid.uuid4().hex[:12]}",
            "deviceId": node_id,
            "displayName": params.get("displayName"),
            "platform": params.get("platform"),
            "version": params.get("version"),
            "coreVersion": params.get("coreVersion"),
            "uiVersion": params.get("uiVersion"),
            "deviceFamily": params.get("deviceFamily"),
            "modelIdentifier": params.get("modelIdentifier"),
            "caps": params.get("caps") if isinstance(params.get("caps"), list) else [],
            "commands": params.get("commands") if isinstance(params.get("commands"), list) else [],
            "permissions": params.get("permissions") if isinstance(params.get("permissions"), dict) else None,
            "pathEnv": str(params.get("pathEnv") or "") or None,
            "remoteIp": params.get("remoteIp"),
            "roles": ["node"],
            "requestedAtMs": now_ms(),
        }
        pending.append(request)
        save_persistent_state("rpc.device_pairs", {"pending": pending, "paired": paired})
        await broadcast_rpc_event("node.pair.requested", request, {"operator"})
        return True, {"status": "pending", "created": True, "request": request}, None

    if method == "node.pair.list":
        pairs = load_device_pairs_state()
        pending = [row for row in pairs["pending"] if "node" in (row.get("roles") or [])]
        paired = [
            row
            for row in pairs["paired"]
            if str(row.get("role") or "").strip() == "node" or "node" in (row.get("roles") or [])
        ]
        return True, {"pending": pending, "paired": paired}, None

    if method == "node.pair.approve":
        request_id = str(params.get("requestId") or "").strip()
        if not request_id:
            return False, None, rpc_error("INVALID_REQUEST", "requestId required", None)
        pairs = load_device_pairs_state()
        pending = pairs["pending"]
        paired = pairs["paired"]
        left = []
        approved = None
        for row in pending:
            if str(row.get("requestId")) == request_id:
                approved = row
                continue
            left.append(row)
        if approved is None:
            return False, None, rpc_error("INVALID_REQUEST", "unknown requestId", None)
        node_id = str(approved.get("deviceId") or "").strip()
        token = f"node_tok_{uuid.uuid4().hex}"
        token_hash = hash_pairing_token(token)
        entry = {
            "deviceId": node_id,
            "displayName": approved.get("displayName"),
            "platform": approved.get("platform"),
            "version": approved.get("version"),
            "coreVersion": approved.get("coreVersion"),
            "uiVersion": approved.get("uiVersion"),
            "deviceFamily": approved.get("deviceFamily"),
            "modelIdentifier": approved.get("modelIdentifier"),
            "remoteIp": approved.get("remoteIp"),
            "caps": approved.get("caps") if isinstance(approved.get("caps"), list) else [],
            "commands": approved.get("commands") if isinstance(approved.get("commands"), list) else [],
            "permissions": approved.get("permissions") if isinstance(approved.get("permissions"), dict) else None,
            "pathEnv": approved.get("pathEnv"),
            "roles": ["node"],
            "role": "node",
            "scopes": ["operator.read"],
            "approvedAtMs": now_ms(),
        }
        paired = [row for row in paired if str(row.get("deviceId")) != node_id]
        paired.append(entry)
        tokens = load_persistent_state("rpc.node_tokens", {})
        if not isinstance(tokens, dict):
            tokens = {}
        tokens[node_id] = {"hash": token_hash, "updatedAtMs": now_ms()}
        save_persistent_state("rpc.node_tokens", tokens)
        save_persistent_state("rpc.device_pairs", {"pending": left, "paired": paired})
        resolved = {"requestId": request_id, "nodeId": node_id, "decision": "approved", "ts": now_ms()}
        await broadcast_rpc_event("node.pair.resolved", resolved, {"operator", "node"})
        return True, {"ok": True, "node": entry, "token": token}, None

    if method == "node.pair.reject":
        request_id = str(params.get("requestId") or "").strip()
        if not request_id:
            return False, None, rpc_error("INVALID_REQUEST", "requestId required", None)
        pairs = load_device_pairs_state()
        pending = pairs["pending"]
        matched = next((row for row in pending if str(row.get("requestId")) == request_id), None)
        if matched is None:
            return False, None, rpc_error("INVALID_REQUEST", "unknown requestId", None)
        left = [row for row in pending if str(row.get("requestId")) != request_id]
        save_persistent_state("rpc.device_pairs", {"pending": left, "paired": pairs["paired"]})
        resolved = {"requestId": request_id, "nodeId": str(matched.get("deviceId") or ""), "decision": "rejected", "ts": now_ms()}
        await broadcast_rpc_event("node.pair.resolved", resolved, {"operator", "node"})
        return True, {"ok": True, **resolved}, None

    if method == "node.pair.verify":
        node_id = str(params.get("nodeId") or client_id or "").strip()
        token = str(params.get("token") or "").strip()
        if not node_id or not token:
            return False, None, rpc_error("INVALID_REQUEST", "nodeId and token required", None)
        tokens = load_persistent_state("rpc.node_tokens", {})
        ok = False
        if isinstance(tokens, dict):
            record = tokens.get(node_id)
            if isinstance(record, dict):
                stored_hash = str(record.get("hash") or "")
                ok = bool(stored_hash) and hmac.compare_digest(stored_hash, hash_pairing_token(token))
            else:
                ok = hmac.compare_digest(str(record or ""), token)
        return True, {"ok": bool(ok), "nodeId": node_id}, None

    return None

