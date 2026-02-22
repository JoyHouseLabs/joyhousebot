"""Connect handshake authorization: device identity, token, rate limit (OpenClaw-aligned)."""

from __future__ import annotations

import hmac
from typing import Any, Callable

from joyhousebot.gateway.device_auth import build_device_auth_payload
from joyhousebot.infra.device_identity import (
    derive_device_id_from_public_key,
    verify_device_signature,
)

DEVICE_SIGNATURE_SKEW_MS = 10 * 60 * 1000  # 10 minutes


def _scopes_allow(requested: list[str], allowed: list[str]) -> bool:
    if not requested:
        return True
    allowed_set = set(allowed)
    return all(s in allowed_set for s in requested)


def _constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def verify_device_token(
    *,
    device_id: str,
    token: str,
    role: str,
    scopes: list[str],
    load_persistent_state: Callable[[str, Any], Any],
    save_persistent_state: Callable[[str, Any], None],
    hash_pairing_token: Callable[[str], str],
    now_ms: Callable[[], int],
) -> tuple[bool, str | None]:
    """Verify device token against stored paired tokens. Returns (ok, reason)."""
    state = load_persistent_state("rpc.device_pairs", {"pending": [], "paired": []})
    paired = state.get("paired") or []
    device_id = (device_id or "").strip()
    role = (role or "operator").strip() or "operator"
    requested_scopes = [s.strip() for s in (scopes or []) if s.strip()]

    for entry in paired:
        if str(entry.get("deviceId") or "").strip() != device_id:
            continue
        tokens = entry.get("tokens") or {}
        token_entry = tokens.get(role)
        if not isinstance(token_entry, dict):
            return False, "token-missing"
        if token_entry.get("revokedAtMs"):
            return False, "token-revoked"
        stored_hash = token_entry.get("tokenHash") or ""
        if not _constant_time_compare(hash_pairing_token(token), stored_hash):
            return False, "token-mismatch"
        allowed_scopes = token_entry.get("scopes") or []
        if not _scopes_allow(requested_scopes, allowed_scopes):
            return False, "scope-mismatch"
        token_entry["lastUsedAtMs"] = now_ms()
        if isinstance(entry.get("tokens"), dict):
            entry["tokens"][role] = token_entry
        save_persistent_state("rpc.device_pairs", {"pending": state.get("pending", []), "paired": paired})
        return True, None
    return False, "device-not-paired"


def try_authorize_connect(
    *,
    params: dict[str, Any],
    client: Any,
    connection_key: str,
    client_host: str | None,
    get_connect_nonce: Callable[[str], str | None],
    config: Any,
    load_persistent_state: Callable[[str, Any], Any],
    save_persistent_state: Callable[[str, Any], None],
    hash_pairing_token: Callable[[str], str],
    now_ms: Callable[[], int],
    rate_limiter: Any | None,
) -> dict[str, Any] | None:
    """
    Validate connect params (device signature, token or shared secret, rate limit).
    On success set client.role, client.scopes, client.client_id, client.connected and return None.
    On failure return error dict.
    """
    role_param = (params.get("role") or "operator").strip() or "operator"
    scopes_param = params.get("scopes")
    if isinstance(scopes_param, list):
        scopes_list = [str(s).strip() for s in scopes_param if str(s).strip()]
    else:
        gateway = getattr(config, "gateway", None)
        scopes_list = list(getattr(gateway, "rpc_default_scopes", []) or [])
    client_id_param = params.get("clientId") or params.get("nodeId") if role_param == "node" else params.get("clientId")
    client_id = str(client_id_param or connection_key).strip() or connection_key

    # Rate limit (shared secret scope for token/password; device token scope when device+token)
    scope_shared = "shared-secret"
    scope_device = "device-token"
    ip = (client_host or "").strip() or "unknown"
    if rate_limiter:
        r = rate_limiter.check(ip, scope_shared)
        if not r.allowed:
            return {"code": "INVALID_REQUEST", "message": "rate_limited", "data": {"retryAfterMs": r.retry_after_ms}}

    device = params.get("device")
    auth = params.get("auth") or {}
    token_auth = (auth.get("token") or "").strip()
    password_auth = (auth.get("password") or "").strip()
    has_shared = bool(token_auth or password_auth)
    gateway = getattr(config, "gateway", None) if config else None
    default_scopes_list = list(getattr(gateway, "rpc_default_scopes", []) or []) if gateway else []
    config_token = getattr(gateway, "control_token", None) or getattr(gateway, "token", None) if gateway else None
    config_password = getattr(gateway, "control_password", None) or getattr(gateway, "password", None) if gateway else None
    control_ui = getattr(gateway, "control_ui", None) if gateway else None
    allow_insecure = getattr(control_ui, "allow_insecure_auth", False) if control_ui else False

    # Shared secret (token or password) - allows connect without device
    if has_shared:
        if config_token and token_auth and _constant_time_compare(token_auth, str(config_token)):
            if rate_limiter:
                rate_limiter.reset(ip, scope_shared)
            client.role = role_param
            client.scopes = set(scopes_list) if scopes_list else set(default_scopes_list)
            client.client_id = client_id
            client.connected = True
            return None
        if config_password and password_auth and _constant_time_compare(password_auth, str(config_password)):
            if rate_limiter:
                rate_limiter.reset(ip, scope_shared)
            client.role = role_param
            client.scopes = set(scopes_list) if scopes_list else set(default_scopes_list)
            client.client_id = client_id
            client.connected = True
            return None
        if rate_limiter:
            rate_limiter.record_failure(ip, scope_shared)
        return {"code": "INVALID_REQUEST", "message": "token_mismatch"}

    # No device: require device identity, or allow if gateway.control_ui.allow_insecure_auth (dev/local only)
    if not device:
        if allow_insecure:
            client.role = role_param
            client.scopes = set(scopes_list) if scopes_list else set(default_scopes_list)
            client.client_id = client_id
            client.connected = True
            return None
        return {"code": "NOT_PAIRED", "message": "device identity required"}

    device_id = (device.get("id") or "").strip()
    public_key = (device.get("publicKey") or "").strip()
    signature = (device.get("signature") or "").strip()
    signed_at = device.get("signedAt")
    nonce = (device.get("nonce") or "").strip() if device.get("nonce") is not None else ""

    if not device_id or not public_key or not signature:
        return {"code": "INVALID_REQUEST", "message": "device identity incomplete"}
    derived_id = derive_device_id_from_public_key(public_key)
    if not derived_id or derived_id != device_id:
        return {"code": "INVALID_REQUEST", "message": "device identity mismatch"}
    if not isinstance(signed_at, (int, float)):
        return {"code": "INVALID_REQUEST", "message": "device signature invalid"}
    now = now_ms()
    if abs(now - signed_at) > DEVICE_SIGNATURE_SKEW_MS:
        return {"code": "INVALID_REQUEST", "message": "device signature expired"}
    connect_nonce = get_connect_nonce(connection_key)
    if connect_nonce and not nonce:
        return {"code": "INVALID_REQUEST", "message": "device nonce required"}
    if connect_nonce and nonce and nonce != connect_nonce:
        return {"code": "INVALID_REQUEST", "message": "device nonce mismatch"}

    client_info = params.get("client") or {}
    client_id_conn = str(client_info.get("id") or "control-ui").strip() or "control-ui"
    client_mode = str(client_info.get("mode") or "webchat").strip() or "webchat"

    payload_v2 = build_device_auth_payload(
        device_id=device_id,
        client_id=client_id_conn,
        client_mode=client_mode,
        role=role_param,
        scopes=scopes_list,
        signed_at_ms=int(signed_at),
        token=token_auth or None,
        nonce=nonce or None,
        version="v2",
    )
    if not verify_device_signature(public_key, payload_v2, signature):
        payload_v1 = build_device_auth_payload(
            device_id=device_id,
            client_id=client_id_conn,
            client_mode=client_mode,
            role=role_param,
            scopes=scopes_list,
            signed_at_ms=int(signed_at),
            token=token_auth or None,
            nonce=None,
            version="v1",
        )
        if not verify_device_signature(public_key, payload_v1, signature):
            return {"code": "INVALID_REQUEST", "message": "device signature invalid"}

    if token_auth:
        if rate_limiter:
            r = rate_limiter.check(ip, scope_device)
            if not r.allowed:
                return {"code": "INVALID_REQUEST", "message": "rate_limited", "data": {"retryAfterMs": r.retry_after_ms}}
        ok, reason = verify_device_token(
            device_id=device_id,
            token=token_auth,
            role=role_param,
            scopes=scopes_list,
            load_persistent_state=load_persistent_state,
            save_persistent_state=save_persistent_state,
            hash_pairing_token=hash_pairing_token,
            now_ms=now_ms,
        )
        if not ok:
            if rate_limiter:
                rate_limiter.record_failure(ip, scope_device)
            return {"code": "INVALID_REQUEST", "message": reason or "device_token_mismatch"}
        if rate_limiter:
            rate_limiter.reset(ip, scope_device)

    client.role = role_param
    client.scopes = set(scopes_list) if scopes_list else set(default_scopes_list)
    client.client_id = client_id
    client.connected = True
    return None
