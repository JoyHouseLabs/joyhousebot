"""Helpers for skills-related HTTP endpoint payloads."""

from __future__ import annotations

from typing import Any, Callable

from joyhousebot.services.skills.skill_service import list_skills_for_api, patch_skill_enabled


def list_skills_response(*, config: Any) -> dict[str, Any]:
    """Build response payload for GET /skills."""
    skills = list_skills_for_api(config)
    return {"ok": True, "skills": skills}


def patch_skill_response(
    *,
    name: str,
    enabled: bool,
    get_cached_config: Callable[..., Any],
    save_config: Callable[[Any], None],
    app_state: dict[str, Any],
) -> dict[str, Any]:
    """Apply skill enabled patch and return response for PATCH /skills/{name}."""
    config = get_cached_config(force_reload=True)
    patch_skill_enabled(
        config=config,
        name=name,
        enabled=enabled,
        save_config=save_config,
        app_state=app_state,
    )
    return {"ok": True, "name": name, "enabled": enabled}
