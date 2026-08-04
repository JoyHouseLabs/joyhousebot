"""Canonical user/session identity helpers for server execution."""

from __future__ import annotations


def require_user_id(value: object, *, source: str = "user_id") -> str:
    """Return a non-empty user ID or reject an unscoped server request."""
    user_id = str(value or "").strip()
    if not user_id:
        raise ValueError(f"{source} is required")
    return user_id


def conversation_key(user_id: str, agent_id: str, session_id: str) -> str:
    """Build an unambiguous internal key for one user's agent session."""
    user = require_user_id(user_id)
    agent = str(agent_id or "").strip()
    session = str(session_id or "").strip()
    if not agent:
        raise ValueError("agent_id is required")
    if not session:
        raise ValueError("session_id is required")
    return f"runtime:{len(user)}:{user}:{len(agent)}:{agent}:{session}"
