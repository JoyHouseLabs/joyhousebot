"""RPC handlers for miscellaneous control/query methods."""

from __future__ import annotations

from typing import Any, Awaitable, Callable


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_misc_method(
    *,
    method: str,
    params: dict[str, Any],
    config: Any,
    app_state: dict[str, Any],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    get_models_payload: Callable[[Any], list[dict[str, Any]]],
    build_auth_profiles_report: Callable[[Any], dict[str, Any]],
    build_actions_catalog: Callable[[], dict[str, Any]],
    validate_action_candidate: Callable[..., dict[str, Any]],
    validate_action_batch: Callable[[list[dict[str, Any]]], dict[str, Any]],
    control_overview: Callable[[], Awaitable[dict[str, Any]]],
    now_ms: Callable[[], int],
    get_alerts_lifecycle_view: Callable[[], dict[str, Any]],
    presence_entries: Callable[[], list[Any]],
    normalize_presence_entry: Callable[[Any], dict[str, Any]],
    get_store: Callable[[], Any],
    load_persistent_state: Callable[[str, Any], Any],
    run_update_install: Callable[[], Awaitable[None]],
    create_task: Callable[[Awaitable[Any]], Any],
) -> RpcResult | None:
    """Handle lightweight misc methods. Return None when method is unrelated."""
    if method == "models.list":
        return True, {"models": get_models_payload(config)}, None

    if method == "auth.profiles.status":
        return True, build_auth_profiles_report(config), None

    if method == "actions.catalog":
        return True, build_actions_catalog(), None

    if method == "actions.validate":
        code = str(params.get("code") or "").strip()
        action = params.get("action")
        payload = validate_action_candidate(code=code, candidate=action if isinstance(action, dict) else None)
        if bool(payload.get("ok")):
            return True, payload, None
        return False, None, rpc_error("INVALID_REQUEST", str(payload.get("reason") or "invalid action"), {"validation": payload})

    if method == "actions.validate.batch":
        raw_items = params.get("items")
        if not isinstance(raw_items, list):
            return False, None, rpc_error("INVALID_REQUEST", "actions.validate.batch requires items[]", None)
        items: list[dict[str, Any]] = []
        for row in raw_items:
            if isinstance(row, dict):
                items.append({"code": str(row.get("code") or ""), "action": row.get("action")})
        return True, validate_action_batch(items), None

    if method == "actions.validate.batch.lifecycle":
        raw_items = params.get("items")
        if not isinstance(raw_items, list):
            return False, None, rpc_error("INVALID_REQUEST", "actions.validate.batch.lifecycle requires items[]", None)
        items: list[dict[str, Any]] = []
        for row in raw_items:
            if isinstance(row, dict):
                items.append({"code": str(row.get("code") or ""), "action": row.get("action")})
        validation = validate_action_batch(items)
        overview = await control_overview()
        payload = {
            "ok": bool(validation.get("ok")),
            "validation": validation,
            "alertsSummary": overview.get("alertsSummary", {}),
            "alertsLifecycle": overview.get("alertsLifecycle", {}),
            "generatedAtMs": now_ms(),
        }
        return True, payload, None

    if method == "alerts.lifecycle":
        return True, get_alerts_lifecycle_view(), None

    if method == "system-presence":
        return True, [normalize_presence_entry(entry) for entry in presence_entries()], None

    if method == "logs.tail":
        cursor = params.get("cursor")
        cursor_i = int(cursor) if isinstance(cursor, int) or (isinstance(cursor, str) and cursor.isdigit()) else None
        limit = int(params.get("limit") or 200)
        tail = get_store().tail_task_events(cursor=cursor_i, limit=limit)
        return True, {"file": str(get_store().db_path), **tail}, None

    if method == "update.run":
        current = load_persistent_state("rpc.update_status", app_state.get("rpc_update_status"))
        if current.get("running"):
            return True, {"ok": True, "started": False, "status": current}, None
        app_state["rpc_update_status"] = current
        create_task(run_update_install())
        return True, {"ok": True, "started": True, "status": current}, None

    if method == "doctor.memory.status":
        return True, {"ok": True, "status": "healthy", "message": "memory diagnostics not implemented"}, None

    if method == "push.test":
        return True, {"ok": True, "delivered": False, "message": "push notifications not configured"}, None

    return None

