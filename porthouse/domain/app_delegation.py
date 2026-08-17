"""Security policy for independent App identity delegation."""

from __future__ import annotations

from typing import Iterable

from porthouse.utils.permissions import permission_granted

DELEGATABLE_APP_SCOPES = frozenset(
    {
        "apps.read",
        "apps.launch",
        "apps.schedules",
        "knowledge.read",
        "knowledge.write",
        "runs.read",
        "runs.write",
        "work_handoffs.read",
        "work_handoffs.write",
    }
)


def normalize_app_scopes(values: Iterable[str]) -> tuple[str, ...]:
    scopes = tuple(
        sorted({str(item).strip().lower() for item in values if str(item).strip()})
    )
    if not scopes:
        raise ValueError("at least one delegated App scope is required")
    unsupported = sorted(set(scopes) - DELEGATABLE_APP_SCOPES)
    if unsupported:
        raise ValueError(f"App delegation contains unsupported scopes: {unsupported}")
    return scopes


def installation_scope_ceiling(granted_permissions: Iterable[str]) -> tuple[str, ...]:
    """Translate approved installation permissions to attenuated API scopes."""
    permissions = tuple(str(item).strip().lower() for item in granted_permissions)
    ceiling = {"apps.read"}
    if any(permission_granted(item, "runs.submit") for item in permissions):
        # App-bound principal checks further constrain these scopes to Runs
        # tagged with the same installation_id.
        ceiling.update({"apps.launch", "runs.read", "runs.write"})
    if any(permission_granted(item, "schedules.submit") for item in permissions):
        # Scheduling an Entry Point is still a Run submission on a timer: the
        # per-tick execution-time authority re-checks runs.submit.
        ceiling.add("apps.schedules")
    if any(
        permission_granted(item, permission)
        for item in permissions
        for permission in ("knowledge.read", "knowledge.write")
    ):
        # App knowledge scopes only unlock the path-scoped App sub-router;
        # the personal /v1/knowledge surface is not reachable with them.
        ceiling.update({"knowledge.read", "knowledge.write"})
    for scope in DELEGATABLE_APP_SCOPES:
        if any(permission_granted(item, scope) for item in permissions):
            ceiling.add(scope)
    return tuple(sorted(ceiling))


__all__ = [
    "DELEGATABLE_APP_SCOPES",
    "installation_scope_ceiling",
    "normalize_app_scopes",
]
