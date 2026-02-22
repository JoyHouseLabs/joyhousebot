"""Shared identity domain operations for HTTP adapters."""

from __future__ import annotations

from typing import Any


def get_identity_http(store: Any) -> dict[str, Any]:
    """Build identity response payload for GET /identity (ok + data)."""
    identity = store.get_identity()
    return {
        "ok": True,
        "data": {
            "identity_public_key": identity.identity_public_key if identity else None,
            "house_id": identity.house_id if identity else None,
            "status": identity.status if identity else "local_only",
            "ws_url": identity.ws_url if identity else None,
            "server_url": identity.server_url if identity else None,
        },
    }
