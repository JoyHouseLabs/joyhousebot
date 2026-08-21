"""Deterministic aggregation contracts for durable multi-agent task graphs.

LLM synthesis remains useful for prose answers, but it must be an explicit
policy rather than the only way a graph can finish.  This module keeps the
non-LLM policies pure so a run can be replayed and audited without a provider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from joyhousebot.domain.aggregation import AggregationPolicy, normalize_aggregation_policy

__all__ = [
    "AggregationPolicy",
    "AggregationResult",
    "aggregate_task_results",
    "normalize_aggregation_policy",
]


@dataclass(frozen=True, slots=True)
class AggregationResult:
    content: str
    structured_output: Any
    audit: dict[str, Any]


def aggregate_task_results(tasks: list[dict[str, Any]], policy: AggregationPolicy) -> AggregationResult:
    """Apply a deterministic aggregation policy to completed task records."""

    all_sources = [_source(task) for task in tasks]
    sources = [source for source in all_sources if source["status"] == "completed"]
    audit: dict[str, Any] = {
        "policy": policy.to_dict(),
        "source_task_ids": [source["task_id"] for source in sources],
        "source_count": len(sources),
        "conflicts": [],
        "discarded": [],
    }
    if policy.mode == "raw":
        value = {source["spec_id"]: source["result"] for source in all_sources}
        return _result(value, audit)
    if policy.mode == "evidence_merge":
        value = {
            "evidence": [
                {
                    "task_id": source["task_id"],
                    "spec_id": source["spec_id"],
                    "agent_id": source["agent_id"],
                    "content": source["content"],
                    "data": source["data"],
                }
                for source in sources[: policy.max_items]
            ]
        }
        audit["discarded"] = [source["task_id"] for source in sources[policy.max_items :]]
        return _result(value, audit)
    if policy.mode == "rank_and_select":
        ranked = sorted(
            sources,
            key=lambda source: (-_score(source["data"], policy.score_path), source["task_id"]),
        )
        selected = ranked[: policy.max_items]
        audit["ranking"] = [
            {"task_id": source["task_id"], "score": _score(source["data"], policy.score_path)}
            for source in ranked
        ]
        audit["discarded"] = [source["task_id"] for source in ranked[policy.max_items :]]
        value = {
            "selected": [_public_source(source) for source in selected],
            "best": _public_source(selected[0]) if selected else None,
        }
        content = (
            str(selected[0]["content"])
            if selected
            else json.dumps(value, ensure_ascii=False, default=str)
        )
        return AggregationResult(content=content, structured_output=value, audit=audit)
    if policy.mode == "structured_merge":
        merged: Any = {}
        for source in sources:
            candidate = source["data"]
            if not isinstance(candidate, (dict, list)):
                audit["discarded"].append(
                    {"task_id": source["task_id"], "reason": "non_structured_output"}
                )
                continue
            merged = _merge(merged, candidate, policy.conflict_resolution, "", source["task_id"], audit)
        return _result(merged, audit)
    raise ValueError(f"policy requires LLM synthesis: {policy.mode}")


def synthesis_prompt(*, goal: str, tasks: list[dict[str, Any]], policy: AggregationPolicy) -> str:
    """Build the bounded, source-labelled prompt used only by llm_synthesis."""

    sources = [_source(task) for task in tasks if str(task.get("status")) == "completed"]
    evidence = [
        {
            "task_id": source["task_id"],
            "spec_id": source["spec_id"],
            "content": source["content"],
            "data": source["data"],
        }
        for source in sources[: policy.max_items]
    ]
    instructions = policy.instructions or (
        "Return a concise, evidence-backed answer. Cite task IDs for material claims. "
        "Do not invent facts not present in task results."
    )
    return (
        f"Synthesize a final answer for this goal: {goal}\n\n"
        f"Aggregation instructions: {instructions}\n\n"
        f"Task evidence:\n{json.dumps(evidence, ensure_ascii=False, default=str)[:50000]}"
    )


def _source(task: dict[str, Any]) -> dict[str, Any]:
    result = dict(task.get("result") or {})
    capability = result.get("capability_result")
    capability_data = capability.get("data") if isinstance(capability, dict) else None
    content = result.get("content")
    data = capability_data if isinstance(capability_data, (dict, list)) else _parse_content(content)
    return {
        "task_id": str(task.get("task_id") or ""),
        "spec_id": str(task.get("spec_id") or task.get("task_id") or ""),
        "agent_id": str(task.get("agent_id") or ""),
        "content": str(content or ""),
        "data": data,
        "result": result,
        "status": str(task.get("status") or result.get("status") or "unknown"),
    }


def _parse_content(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return value
    return decoded if isinstance(decoded, (dict, list)) else value


def _merge(current: Any, incoming: Any, resolution: str, path: str, task_id: str, audit: dict[str, Any]) -> Any:
    if isinstance(current, dict) and isinstance(incoming, dict):
        value = dict(current)
        for key in sorted(incoming):
            child_path = f"{path}.{key}" if path else key
            if key not in value:
                value[key] = incoming[key]
            else:
                value[key] = _merge(value[key], incoming[key], resolution, child_path, task_id, audit)
        return value
    if isinstance(current, list) and isinstance(incoming, list):
        value = list(current)
        known = {_canonical(item) for item in value}
        for item in incoming:
            encoded = _canonical(item)
            if encoded not in known:
                value.append(item)
                known.add(encoded)
        return value
    if current == incoming:
        return current
    chosen = incoming if resolution == "prefer_last" else current
    audit["conflicts"].append(
        {"path": path or "$", "task_id": task_id, "resolution": resolution, "chosen": chosen}
    )
    return chosen


def _score(value: Any, path: str) -> float:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return 0.0
        current = current.get(part)
    try:
        return float(current)
    except (TypeError, ValueError):
        return 0.0


def _public_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": source["task_id"],
        "spec_id": source["spec_id"],
        "agent_id": source["agent_id"],
        "content": source["content"],
        "data": source["data"],
    }


def _result(value: Any, audit: dict[str, Any]) -> AggregationResult:
    return AggregationResult(
        content=json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        structured_output=value,
        audit=audit,
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
