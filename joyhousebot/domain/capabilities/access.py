"""Agent-facing capability admission and explicit-grant policy."""

from __future__ import annotations

from typing import Any, Iterable


def _value(definition: Any, name: str, default: Any = None) -> Any:
    if isinstance(definition, dict):
        return definition.get(name, default)
    return getattr(definition, name, default)


def capability_id(definition: Any) -> str:
    reference = _value(definition, "ref", {})
    if isinstance(reference, dict):
        return str(reference.get("capability_id") or "").strip()
    return str(getattr(reference, "capability_id", "") or "").strip()


def capability_kind(definition: Any) -> str:
    reference = _value(definition, "ref", {})
    if isinstance(reference, dict):
        return str(reference.get("kind") or "").strip()
    value = getattr(reference, "kind", "")
    return str(getattr(value, "value", value) or "").strip()


def requires_explicit_grant(definition: Any) -> bool:
    """Return whether catalog mode must not grant this capability implicitly."""
    cost_policy = _value(definition, "cost_policy", {})
    if not isinstance(cost_policy, dict):
        cost_policy = {}
    tags = {
        str(item).strip().lower()
        for item in (_value(definition, "tags", ()) or ())
        if str(item).strip()
    }
    return bool(
        cost_policy.get("explicit_grant_required")
        or cost_policy.get("approval_required")
        or cost_policy.get("metered_external_service")
        or "cost-bearing" in tags
        or str(_value(definition, "side_effect", "none")).strip().lower()
        == "external"
        or str(_value(definition, "data_classification", "internal")).strip().lower()
        == "restricted"
    )


def resolve_capability_policy(
    policy: dict[str, Any] | None,
    definitions: Iterable[Any],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Resolve one Agent policy into a frozen set of current capability ids.

    Catalog mode includes ordinary capabilities plus explicitly selected
    protected capabilities. Allowlist mode includes only selected ids.
    """
    value = dict(policy or {})
    mode = str(value.get("mode") or "catalog").strip().lower()
    if mode not in {"catalog", "allowlist"}:
        raise ValueError("capability_policy.mode must be catalog or allowlist")
    raw_allowed = value.get("allowed") or ()
    if not isinstance(raw_allowed, (list, tuple, set, frozenset)):
        raise ValueError("capability_policy.allowed must be an array")
    allowed = list(
        dict.fromkeys(str(item).strip() for item in raw_allowed if str(item).strip())
    )
    catalog = [item for item in definitions if capability_id(item)]
    by_id = {capability_id(item): item for item in catalog}
    missing = [item for item in allowed if item not in by_id]
    if strict and missing:
        raise ValueError(
            "Agent capability policy references unpublished capabilities: "
            + ", ".join(missing)
        )
    selected = set(allowed) & set(by_id)
    if mode == "catalog":
        selected.update(
            capability_id(item)
            for item in catalog
            if not requires_explicit_grant(item)
        )
    resolved = [capability_id(item) for item in catalog if capability_id(item) in selected]
    return {
        **value,
        "mode": mode,
        "allowed": allowed,
        "resolved": resolved,
        "resolution_version": 1,
    }


def executable_capability_ids(
    policy: dict[str, Any] | None, definitions: Iterable[Any]
) -> list[str]:
    kinds = {"capability", "connector"}
    catalog = list(definitions)
    resolved = set(resolve_capability_policy(policy, catalog)["resolved"])
    return [
        capability_id(item)
        for item in catalog
        if capability_id(item) in resolved and capability_kind(item) in kinds
    ]


__all__ = [
    "capability_id",
    "capability_kind",
    "executable_capability_ids",
    "requires_explicit_grant",
    "resolve_capability_policy",
]
