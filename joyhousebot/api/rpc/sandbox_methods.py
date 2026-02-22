"""RPC handlers for sandbox.list, sandbox.recreate, sandbox.explain."""

from __future__ import annotations

from typing import Any, Callable


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_sandbox_method(
    *,
    method: str,
    params: dict[str, Any],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    load_persistent_state: Callable[[str, Any], Any],
    save_persistent_state: Callable[[str, Any], None],
) -> RpcResult | None:
    """Handle sandbox.list, sandbox.recreate, sandbox.explain. Return None when method is unrelated."""
    from joyhousebot.sandbox.service import explain_local, list_containers_local, recreate_containers_local

    if method == "sandbox.list":
        browser_only = bool(params.get("browser", False))
        items = list_containers_local(load_persistent_state, browser_only=browser_only)
        return True, {"items": items}, None

    if method == "sandbox.recreate":
        all_items = bool(params.get("all", False))
        session = str(params.get("session") or "").strip() or None
        agent = str(params.get("agent") or "").strip() or None
        browser_only = bool(params.get("browser", False))
        force = bool(params.get("force", False))
        result = recreate_containers_local(
            load_persistent_state,
            save_persistent_state,
            all_items=all_items,
            session=session or "",
            agent=agent or "",
            browser_only=browser_only,
            force=force,
        )
        return True, result, None

    if method == "sandbox.explain":
        session = str(params.get("session") or "").strip()
        agent = str(params.get("agent") or "").strip()
        payload = explain_local(load_persistent_state, session=session, agent=agent)
        return True, payload, None

    return None
