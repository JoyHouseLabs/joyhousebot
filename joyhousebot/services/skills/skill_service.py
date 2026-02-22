"""Shared skills domain operations for HTTP/RPC adapters."""

from __future__ import annotations

from typing import Any, Callable


def build_skills_status_report(config: Any) -> dict[str, Any]:
    """Build RPC skills.status payload (workspace, managedSkillsDir, skills list)."""
    from joyhousebot.agent.skills import BUILTIN_SKILLS_DIR, SkillsLoader

    workspace = config.workspace_path
    loader = SkillsLoader(workspace, BUILTIN_SKILLS_DIR)
    skills = []
    for s in loader.list_skills(filter_unavailable=False):
        name = s["name"]
        desc = loader._get_skill_description(name) or ""
        entry = (config.skills.entries or {}).get(name)
        enabled = getattr(entry, "enabled", True) if entry else True
        skills.append(
            {
                "name": name,
                "description": desc,
                "source": s.get("source", "workspace"),
                "filePath": s.get("path", ""),
                "baseDir": str(workspace),
                "skillKey": name,
                "always": False,
                "disabled": not enabled,
                "blockedByAllowlist": False,
                "eligible": True,
                "requirements": {"bins": [], "env": [], "config": [], "os": []},
                "missing": {"bins": [], "env": [], "config": [], "os": []},
                "configChecks": [],
                "install": [],
            }
        )
    return {
        "workspaceDir": str(workspace),
        "managedSkillsDir": str(workspace / "skills"),
        "skills": skills,
    }


def list_skills_for_api(config: Any) -> list[dict[str, Any]]:
    """Build GET /skills list (name, source, description, available, enabled)."""
    from joyhousebot.agent.skills import BUILTIN_SKILLS_DIR, SkillsLoader

    workspace = config.workspace_path
    loader = SkillsLoader(workspace, BUILTIN_SKILLS_DIR)
    entries = getattr(config.skills, "entries", None) or {}
    raw_skills = loader.list_skills(filter_unavailable=False)
    out = []
    for s in raw_skills:
        name = s["name"]
        meta = loader._get_skill_meta(name)
        available = loader._check_requirements(meta)
        entry = entries.get(name)
        enabled = getattr(entry, "enabled", True) if entry else True
        desc = loader._get_skill_description(name)
        out.append({
            "name": name,
            "source": s["source"],
            "description": desc,
            "available": available,
            "enabled": enabled,
        })
    return out


def patch_skill_enabled(
    *,
    config: Any,
    name: str,
    enabled: bool,
    save_config: Callable[[Any], None],
    app_state: dict[str, Any],
) -> None:
    """Update skill enabled state in config and persist."""
    from joyhousebot.config.schema import SkillEntryConfig

    if config.skills.entries is None:
        config.skills.entries = {}
    existing = config.skills.entries.get(name)
    existing_env = getattr(existing, "env", None) if existing else None
    config.skills.entries[name] = SkillEntryConfig(enabled=enabled, env=existing_env)
    save_config(config)
    if "config" in app_state:
        app_state["config"] = config
