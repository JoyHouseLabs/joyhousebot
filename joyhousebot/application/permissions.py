"""Central permission catalog for the platform control surface."""

from __future__ import annotations

from typing import Any

PERMISSION_CATALOG: dict[str, dict[str, str]] = {
    "platform.read": {"group": "platform", "description": "View platform overview"},
    "runs.read": {"group": "runtime", "description": "View all users' runs"},
    "runs.cancel": {"group": "runtime", "description": "Cancel any active run"},
    "workers.read": {"group": "runtime", "description": "View cluster workers"},
    "agents.read": {"group": "catalog", "description": "View Agent definitions"},
    "agents.write": {"group": "catalog", "description": "Create Agent drafts"},
    "agents.publish": {"group": "catalog", "description": "Publish Agent revisions"},
    "capabilities.read": {"group": "catalog", "description": "View capabilities"},
    "capabilities.publish": {"group": "catalog", "description": "Publish capabilities"},
    "scenarios.read": {"group": "scenarios", "description": "View and simulate scenarios"},
    "scenarios.write": {"group": "scenarios", "description": "Create scenario drafts"},
    "scenarios.publish": {"group": "scenarios", "description": "Publish scenarios"},
    "evals.read": {"group": "quality", "description": "View evaluation evidence"},
    "evals.write": {"group": "quality", "description": "Manage evaluations and gates"},
    "settings.read": {"group": "settings", "description": "View safe settings summary"},
    "settings.write": {"group": "settings", "description": "Change platform settings"},
    "admins.read": {"group": "access", "description": "View platform administrators"},
    "admins.write": {"group": "access", "description": "Manage platform administrators"},
    "tokens.read": {"group": "access", "description": "View API access tokens"},
    "tokens.write": {"group": "access", "description": "Issue and revoke API access tokens"},
    "audit.read": {"group": "audit", "description": "View immutable control events"},
    "rollouts.read": {"group": "audit", "description": "View configuration rollouts"},
    "reasoning.read": {"group": "diagnostics", "description": "View captured reasoning"},
    "reasoning.read_raw": {"group": "diagnostics", "description": "View raw trace payloads"},
    "replay.read": {"group": "diagnostics", "description": "View replay experiments"},
    "replay.execute": {"group": "diagnostics", "description": "Execute replay experiments"},
}


ROLE_PERMISSION_SETS: dict[str, tuple[str, ...]] = {
    "viewer": (
        "platform.read",
        "runs.read",
        "workers.read",
        "agents.read",
        "capabilities.read",
        "scenarios.read",
        "evals.read",
        "settings.read",
        "rollouts.read",
        "replay.read",
    ),
    "operator": (
        "platform.read",
        "runs.read",
        "runs.cancel",
        "workers.read",
        "agents.read",
        "capabilities.read",
        "scenarios.read",
        "evals.read",
        "settings.read",
        "audit.read",
        "tokens.read",
        "rollouts.read",
        "reasoning.read",
        "replay.read",
        "replay.execute",
    ),
    "admin": tuple(PERMISSION_CATALOG),
}


def normalize_permissions(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted({str(item).strip() for item in values if str(item).strip()}))
    unknown = set(normalized) - set(PERMISSION_CATALOG) - {"*"}
    if unknown:
        raise ValueError(f"unknown platform permissions: {sorted(unknown)}")
    return normalized


def permission_catalog_response() -> dict[str, Any]:
    return {
        "items": [
            {"permission": permission, **metadata}
            for permission, metadata in PERMISSION_CATALOG.items()
        ],
        "roles": {role: list(values) for role, values in ROLE_PERMISSION_SETS.items()},
    }
