"""Helpers for resolving API agents and agent HTTP endpoint payloads."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException

from joyhousebot.services.agents.agent_service import (
    get_default_agent_http,
    list_agents_http,
    patch_agent_activated_http,
)
from joyhousebot.services.errors import ServiceError


def resolve_agent_or_503(*, agent_id: str | None, resolve_agent: Callable[[str | None], Any]) -> Any:
    """Resolve agent and raise OpenClaw-compatible 503 when unavailable."""
    agent = resolve_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    return agent


def get_agent_response(*, config: Any) -> dict[str, Any]:
    """Build response for GET /agent (default agent info)."""
    return get_default_agent_http(config)


def list_agents_response(*, config: Any) -> dict[str, Any]:
    """Build response for GET /agents."""
    return list_agents_http(config)


def patch_agent_response(
    *,
    agent_id: str,
    activated: bool | None,
    get_cached_config: Callable[..., Any],
    save_config: Callable[[Any], None],
    app_state: dict[str, Any],
) -> dict[str, Any]:
    """Apply PATCH /agents/{agent_id} (activated only) and return response."""
    config = get_cached_config(force_reload=True)
    try:
        return patch_agent_activated_http(
            config=config,
            agent_id=agent_id,
            activated=activated,
            save_config=save_config,
            app_state=app_state,
        )
    except ServiceError as exc:
        if exc.code == "NOT_FOUND":
            raise HTTPException(status_code=404, detail=exc.message) from exc
        raise HTTPException(status_code=400, detail=exc.message) from exc

