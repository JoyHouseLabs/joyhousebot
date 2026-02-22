"""Helpers for sandbox REST endpoints (containers list, explain, recreate)."""

from __future__ import annotations

from typing import Any, Callable

from joyhousebot.sandbox.service import (
    explain_local,
    list_containers_local,
    recreate_containers_local,
)


def list_sandbox_containers_response(
    *,
    load_persistent_state: Callable[[str, Any], Any],
    browser_only: bool = False,
) -> dict[str, Any]:
    """Build response for GET /sandbox/containers."""
    items = list_containers_local(load_persistent_state, browser_only=browser_only)
    return {"ok": True, "items": items}


def sandbox_explain_response(
    *,
    load_persistent_state: Callable[[str, Any], Any],
    session: str = "",
    agent: str = "",
) -> dict[str, Any]:
    """Build response for GET /sandbox/explain."""
    return explain_local(
        load_persistent_state,
        session=(session or "").strip() or "agent:main:main",
        agent=(agent or "").strip(),
    )


def sandbox_recreate_response(
    *,
    load_persistent_state: Callable[[str, Any], Any],
    save_persistent_state: Callable[[str, Any], None],
    all_items: bool = False,
    session: str | None = None,
    agent: str | None = None,
    browser_only: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Build response for POST /sandbox/recreate."""
    return recreate_containers_local(
        load_persistent_state,
        save_persistent_state,
        all_items=all_items,
        session=session or "",
        agent=agent or "",
        browser_only=browser_only,
        force=force,
    )
