"""Safe scheduling for independent Tool calls returned by one model turn.

The LLM may return several function calls at once.  Those calls have no
result dependency yet, but not every capability is safe to overlap.  This
module is intentionally small and deterministic so its decisions are easy to
test and expose in a Run trace.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Sequence


@dataclass(frozen=True, slots=True)
class ToolExecutionBatch:
    """Indices from the provider response that may execute together."""

    indices: tuple[int, ...]
    parallel: bool


def normalize_tool_execution_policy(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, dict) else {}
    mode = str(raw.get("mode") or "sequential").strip().lower()
    if mode not in {"sequential", "parallel_safe"}:
        mode = "sequential"
    try:
        limit = int(raw.get("max_parallel_calls") or raw.get("max_concurrent") or 1)
    except (TypeError, ValueError):
        limit = 1
    return {"mode": mode, "max_parallel_calls": max(1, min(limit, 128))}


def resolve_tool_execution_policy(
    agent_policy: Any,
    scenario_execution_policy: Any,
) -> dict[str, Any]:
    """Resolve the opt-in policy, with a scenario able to tighten/override it."""
    agent = normalize_tool_execution_policy(
        dict(agent_policy or {}).get("tool_execution")
        if isinstance(agent_policy, dict)
        else None
    )
    scenario_raw = (
        dict(scenario_execution_policy or {}).get("tool_execution")
        if isinstance(scenario_execution_policy, dict)
        else None
    )
    if not isinstance(scenario_raw, dict):
        return agent
    scenario = normalize_tool_execution_policy(scenario_raw)
    # Explicit serial scenarios are barriers even if their Agent normally
    # allows parallel read operations.  Parallel scenarios use the stricter
    # of the two declared limits when the Agent also opted in.
    if scenario["mode"] == "sequential" or agent["mode"] == "sequential":
        return {"mode": "sequential", "max_parallel_calls": 1}
    return {
        "mode": "parallel_safe",
        "max_parallel_calls": min(agent["max_parallel_calls"], scenario["max_parallel_calls"]),
    }


def build_tool_execution_batches(
    tool_calls: Sequence[Any],
    *,
    agent_policy: Any,
    scenario_execution_policy: Any,
    capability_policy_for: Callable[[str], dict[str, Any]],
) -> list[ToolExecutionBatch]:
    """Partition ordered tool calls into serial barriers and safe batches.

    A capability is eligible only when it is declared idempotent and has no
    side effect.  Results are always written back in input order by the
    caller, regardless of completion order within a parallel batch.
    """
    policy = resolve_tool_execution_policy(agent_policy, scenario_execution_policy)
    if policy["mode"] != "parallel_safe" or policy["max_parallel_calls"] <= 1:
        return [ToolExecutionBatch((index,), False) for index in range(len(tool_calls))]

    batches: list[ToolExecutionBatch] = []
    current: list[int] = []
    current_counts: Counter[str] = Counter()

    def flush() -> None:
        nonlocal current, current_counts
        if current:
            batches.append(ToolExecutionBatch(tuple(current), len(current) > 1))
        current = []
        current_counts = Counter()

    for index, call in enumerate(tool_calls):
        name = str(getattr(call, "name", "") or "").strip()
        declaration = capability_policy_for(name) if name else {}
        mode = str(declaration.get("mode") or "sequential")
        side_effect = str(declaration.get("side_effect") or "unknown")
        idempotent = bool(declaration.get("idempotent", False))
        try:
            capability_limit = max(1, int(declaration.get("max_concurrent") or 1))
        except (TypeError, ValueError):
            capability_limit = 1
        parallel_safe = (
            bool(name)
            and mode == "parallel_safe"
            and idempotent
            and side_effect == "none"
            and capability_limit > 1
        )
        if not parallel_safe:
            flush()
            batches.append(ToolExecutionBatch((index,), False))
            continue
        if (
            len(current) >= policy["max_parallel_calls"]
            or current_counts[name] >= capability_limit
        ):
            flush()
        current.append(index)
        current_counts[name] += 1
    flush()
    return batches
