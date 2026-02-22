"""Protocol-level RPC helpers for gateway-compatible methods."""

from __future__ import annotations

from typing import Any

from joyhousebot.cli.shared.rpc_utils import rpc_call


class ProtocolService:
    """Thin wrapper for calling gateway RPC methods."""

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return rpc_call(method, params or {})

