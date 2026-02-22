"""Helpers for session-related HTTP endpoint payloads."""

from __future__ import annotations

from typing import Any

from joyhousebot.services.sessions.session_service import (
    delete_session_http,
    get_session_history_http,
    list_sessions_http,
)

def list_sessions_response(*, agent: Any) -> dict[str, Any]:
    """Build response payload for /sessions."""
    return list_sessions_http(agent=agent)


def get_session_history_response(*, agent: Any, session_key: str) -> dict[str, Any]:
    """Build response payload for /sessions/{session_key}/history."""
    return get_session_history_http(agent=agent, session_key=session_key)


def delete_session_response(*, agent: Any, session_key: str) -> dict[str, Any]:
    """Build response payload for deleting a session."""
    return delete_session_http(agent=agent, session_key=session_key)

