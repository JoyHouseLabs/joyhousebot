"""Shared permission-grant matching.

Single source of truth for how a granted permission satisfies a required
permission. Every enforcement point (``Principal.can``, the capability
dispatcher, the tool registry, the plugin registry, the MCP gateway) must
use these helpers so the semantics cannot drift.

Matching rules for one grant vs. one required permission:

- ``*``: global wildcard, grants every permission.
- ``namespace.*``: prefix wildcard, grants every permission inside that
  namespace (e.g. ``runs.*`` grants ``runs.read`` but not ``runsx.read``
  and not the bare ``runs``).
- otherwise: exact string match only.

Everything is fail-closed: empty or ambiguous grants never match, and a
requirement set is satisfied only when *every* required permission is
covered (AND semantics).
"""

from __future__ import annotations

from collections.abc import Iterable

GLOBAL_WILDCARD = "*"
NAMESPACE_WILDCARD_SUFFIX = ".*"


def permission_granted(grant: str, permission: str) -> bool:
    """Return True when a single ``grant`` covers ``permission``."""
    grant = str(grant).strip()
    permission = str(permission).strip()
    if not grant or not permission:
        return False
    if grant == GLOBAL_WILDCARD or grant == permission:
        return True
    if grant.endswith(NAMESPACE_WILDCARD_SUFFIX) and len(grant) > len(NAMESPACE_WILDCARD_SUFFIX):
        # Keep the trailing dot: "runs.*" -> prefix "runs.", so only
        # permissions inside the namespace match, never siblings like
        # "runsx.read" or the bare namespace name itself.
        return permission.startswith(grant[:-1])
    return False


def missing_permissions(
    granted: Iterable[str], required: Iterable[str]
) -> list[str]:
    """Sorted list of required permissions not covered by any grant.

    An empty result means the requirement set is fully satisfied.
    """
    grants = [str(item) for item in granted]
    needed = sorted({str(item).strip() for item in required if str(item).strip()})
    return [
        permission
        for permission in needed
        if not any(permission_granted(grant, permission) for grant in grants)
    ]


def permissions_satisfied(
    granted: Iterable[str], required: Iterable[str]
) -> bool:
    """Return True when every required permission is covered (AND semantics)."""
    return not missing_permissions(granted, required)
