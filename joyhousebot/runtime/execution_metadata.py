"""Build the immutable metadata exposed to one Agent execution context."""

from __future__ import annotations

from typing import Any

_INTERNAL_KEYS = {
    "_runtime_initial_events_required",
    "_runtime_schedule_submission_ready",
}


def build_execution_metadata(
    metadata: dict[str, Any] | None,
    *,
    scenario_state: Any,
    scenario_execution_policy: dict[str, Any],
) -> dict[str, Any]:
    """Preserve accepted inputs while hiding Runtime-only claim fences."""
    value = {
        key: item
        for key, item in dict(metadata or {}).items()
        if key not in _INTERNAL_KEYS
    }
    value.update(
        {
            "scenario_id": str(getattr(scenario_state, "scenario_id", "") or ""),
            "scenario_version": int(
                getattr(scenario_state, "scenario_version", 0) or 0
            ),
            "scenario_inputs": dict(
                getattr(scenario_state, "collected_inputs", {}) or {}
            ),
            "scenario_execution_policy": scenario_execution_policy,
        }
    )
    return value
