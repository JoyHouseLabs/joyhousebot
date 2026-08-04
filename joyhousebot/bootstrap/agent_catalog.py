"""Database-backed Agent catalog helpers shared by all process roles."""

from typing import Any


def default_agent_id(store: Any) -> str:
    profile = store.get_agent_profile()
    if profile is None:
        raise RuntimeError("no active published default Agent exists")
    return profile.definition.agent_id
