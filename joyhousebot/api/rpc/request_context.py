"""Adapters for common RPC request-scoped callbacks."""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable


def make_rpc_error_adapter(
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
) -> Callable[[str, str, dict[str, Any] | None], dict[str, Any]]:
    """Normalize rpc_error signature for handler modules."""

    def _adapter(code: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        return rpc_error(code, message, data)

    return _adapter


def make_broadcast_rpc_event_adapter(
    broadcaster: Callable[[str, Any, set[str] | None], Awaitable[None]],
) -> Callable[[str, Any, set[str] | None], Awaitable[None]]:
    """Normalize broadcast callback signature for handler modules."""

    async def _adapter(event: str, payload: Any, roles: set[str] | None = None) -> None:
        await broadcaster(event, payload, roles=roles)

    return _adapter


def resolve_browser_control_url() -> str:
    """Resolve browser control URL from env with compatibility fallback."""
    return str(os.getenv("JOYHOUSE_BROWSER_CONTROL_URL") or os.getenv("OPENCLAW_BROWSER_CONTROL_URL") or "").strip()


def make_connect_logger(
    log_info: Callable[[str, Any, Any, Any], None],
) -> Callable[[str, list[str], str], None]:
    """Create connect logger callback with stable formatting."""

    def _logger(role: str, scopes: list[str], client_id: str) -> None:
        log_info("RPC connect role={} scopes={} client={}", role, scopes, client_id)

    return _logger

