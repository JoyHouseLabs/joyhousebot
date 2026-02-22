"""RPC handlers for connect handshake methods."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from joyhousebot.gateway.connect_auth import try_authorize_connect


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_connect_method(
    *,
    method: str,
    params: dict[str, Any],
    client: Any,
    connection_key: str,
    client_host: str | None,
    config: Any,
    get_connect_nonce: Callable[[str], str | None],
    rate_limiter: Any,
    load_persistent_state: Callable[[str, Any], Any],
    save_persistent_state: Callable[[str, Any], None],
    hash_pairing_token: Callable[[str], str],
    now_ms: Callable[[], int],
    resolve_agent: Callable[[Any], Any | None],
    build_sessions_list_payload: Callable[[Any, Any], dict[str, Any]],
    control_overview: Callable[[], Awaitable[dict[str, Any]]],
    gateway_methods_with_plugins: Callable[[], list[str]],
    gateway_events: list[str],
    presence_entries: Callable[[], list[Any]],
    normalize_presence_entry: Callable[[Any], dict[str, Any]],
    build_actions_catalog: Callable[[], Any],
    resolve_canvas_host_url: Callable[[Any], str],
    log_connect: Callable[[str, list[str], str], None],
) -> RpcResult | None:
    """Handle connect method. Runs connect auth first; on success client is set by try_authorize_connect."""
    if method != "connect":
        return None

    auth_error = try_authorize_connect(
        params=params,
        client=client,
        connection_key=connection_key,
        client_host=client_host,
        get_connect_nonce=get_connect_nonce,
        config=config,
        load_persistent_state=load_persistent_state,
        save_persistent_state=save_persistent_state,
        hash_pairing_token=hash_pairing_token,
        now_ms=now_ms,
        rate_limiter=rate_limiter,
    )
    if auth_error is not None:
        return False, None, auth_error

    agent = resolve_agent(None)
    session_defaults = {
        "defaultAgentId": config.get_default_agent_id(),
        "mainKey": "main",
        "mainSessionKey": "main",
        "scope": "global",
    }
    if agent:
        sessions_payload = build_sessions_list_payload(agent, config)
        if sessions_payload["sessions"]:
            session_defaults["mainSessionKey"] = sessions_payload["sessions"][0]["key"]

    overview = await control_overview()
    actions_catalog = overview.get("actionsCatalog") if isinstance(overview, dict) else None
    if not isinstance(actions_catalog, dict):
        actions_catalog = build_actions_catalog()
    payload = {
        "type": "hello-ok",
        "protocol": 3,
        "features": {"methods": gateway_methods_with_plugins(), "events": gateway_events},
        "snapshot": {
            "presence": [normalize_presence_entry(e) for e in presence_entries()],
            "health": overview,
            "alerts": overview.get("alerts", []),
            "alertsSummary": overview.get("alertsSummary", {}),
            "alertsLifecycle": overview.get("alertsLifecycle", {}),
            "actionsCatalog": actions_catalog,
            "authProfiles": overview.get("authProfiles", {}),
            "sessionDefaults": session_defaults,
        },
        "auth": {
            "role": client.role,
            "scopes": sorted(client.scopes),
            "issuedAtMs": now_ms(),
        },
        "canvasHostUrl": resolve_canvas_host_url(config),
        "policy": {"tickIntervalMs": 10000},
    }
    log_connect(client.role, sorted(client.scopes), client.client_id)
    return True, payload, None

