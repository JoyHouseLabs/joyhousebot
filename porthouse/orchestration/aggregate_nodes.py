"""Closed-world validation for explicit Graph aggregate nodes."""

from __future__ import annotations

from typing import Any

from porthouse.orchestration.aggregation import normalize_aggregation_policy

_POLICY_KEYS = {
    "mode",
    "version",
    "conflict_resolution",
    "score_path",
    "max_items",
    "instructions",
}


def validate_aggregate_node(task: Any) -> None:
    if not task.dependencies:
        raise ValueError(f"aggregate '{task.id}' requires at least one dependency")
    if len(task.dependencies) > 128:
        raise ValueError(f"aggregate '{task.id}' exceeds the 128 source limit")
    configuration = dict(task.aggregate or {})
    unknown = set(configuration) - _POLICY_KEYS
    if unknown:
        raise ValueError(
            f"aggregate '{task.id}' has unsupported fields: {sorted(unknown)}"
        )
    policy = normalize_aggregation_policy(configuration, aggregate=True)
    if len(policy.instructions) > 8_000:
        raise ValueError(f"aggregate '{task.id}' instructions exceed 8000 characters")
