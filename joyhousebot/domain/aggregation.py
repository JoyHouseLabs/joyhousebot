"""Deterministic aggregation policy contract for task graphs and scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_MODES = {"llm_synthesis", "structured_merge", "evidence_merge", "rank_and_select", "raw"}
_CONFLICTS = {"prefer_first", "prefer_last"}


@dataclass(frozen=True, slots=True)
class AggregationPolicy:
    mode: str = "llm_synthesis"
    version: str = "v1"
    conflict_resolution: str = "prefer_first"
    score_path: str = "score"
    max_items: int = 20
    instructions: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "version": self.version,
            "conflict_resolution": self.conflict_resolution,
            "score_path": self.score_path,
            "max_items": self.max_items,
            "instructions": self.instructions,
        }


def normalize_aggregation_policy(
    value: dict[str, Any] | None, *, aggregate: bool = True
) -> AggregationPolicy:
    """Validate a persisted policy and apply the explicit product default."""

    raw = dict(value or {})
    mode = str(raw.get("mode") or ("llm_synthesis" if aggregate else "raw")).strip()
    if mode not in _MODES:
        raise ValueError(f"unsupported aggregation policy mode: {mode}")
    conflict_resolution = str(raw.get("conflict_resolution") or "prefer_first").strip()
    if conflict_resolution not in _CONFLICTS:
        raise ValueError(f"unsupported aggregation conflict_resolution: {conflict_resolution}")
    max_items = int(raw.get("max_items") or 20)
    if not 1 <= max_items <= 1_000:
        raise ValueError("aggregation policy max_items must be between 1 and 1000")
    score_path = str(raw.get("score_path") or "score").strip()
    if not score_path:
        raise ValueError("aggregation policy score_path is required")
    return AggregationPolicy(
        mode=mode,
        version=str(raw.get("version") or "v1").strip() or "v1",
        conflict_resolution=conflict_resolution,
        score_path=score_path,
        max_items=max_items,
        instructions=str(raw.get("instructions") or "").strip(),
    )
