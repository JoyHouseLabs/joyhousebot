"""Helpers for identity HTTP endpoint payloads."""

from __future__ import annotations

from typing import Any

from joyhousebot.services.identity.identity_service import get_identity_http


def get_identity_response(*, store: Any) -> dict[str, Any]:
    """Build identity response payload for GET /identity."""
    return get_identity_http(store)
