"""Closed-world validation for Graph failure and automatic Saga policies."""

from __future__ import annotations

from typing import Any

from joyhousebot.domain.capabilities import CapabilityRef
from joyhousebot.orchestration.control_nodes import control_source_id

_POLICY_KEYS = {"mode", "on_compensation_failure"}
_MODES = {"fail_fast", "continue", "saga"}


def normalize_failure_policy(value: dict[str, Any], *, fail_fast: bool) -> dict[str, Any]:
    if not value:
        return {
            "mode": "fail_fast" if fail_fast else "continue",
            "on_compensation_failure": "stop",
        }
    unknown = set(value) - _POLICY_KEYS
    if unknown:
        raise ValueError(f"failure_policy has unsupported fields: {sorted(unknown)}")
    mode = str(value.get("mode") or "")
    if mode not in _MODES:
        raise ValueError("failure_policy mode must be fail_fast, continue, or saga")
    compensation_failure = str(value.get("on_compensation_failure") or "stop")
    if compensation_failure != "stop":
        raise ValueError("failure_policy on_compensation_failure currently requires stop")
    return {"mode": mode, "on_compensation_failure": compensation_failure}


def validate_saga_declarations(
    tasks: list[Any],
    catalog: list[dict[str, Any]],
    policy: dict[str, Any],
    *,
    max_concurrent: int,
) -> None:
    normalized = normalize_failure_policy(policy, fail_fast=True)
    if normalized["mode"] != "saga":
        return
    if max_concurrent != 1:
        raise ValueError("saga failure_policy requires max_concurrent=1")
    normal = [task for task in tasks if task.node_type != "compensation"]
    compensations = [task for task in tasks if task.node_type == "compensation"]
    if not normal or not compensations:
        raise ValueError("saga failure_policy requires work and compensation nodes")
    unsupported = [task.id for task in normal if task.node_type != "capability"]
    if unsupported:
        raise ValueError(
            "saga failure_policy initially supports only direct capability work nodes: "
            f"{sorted(unsupported)}"
        )
    for index, task in enumerate(normal):
        expected = [] if index == 0 else [normal[index - 1].id]
        if list(task.dependencies) != expected:
            raise ValueError("saga failure_policy requires one serial capability chain")

    definitions = {CapabilityRef.from_dict(dict(item["ref"])).identity: item for item in catalog}
    declarations: dict[str, Any] = {}
    for task in compensations:
        source_id = control_source_id(task.compensation)
        if source_id in declarations:
            raise ValueError(f"saga source '{source_id}' has multiple compensation nodes")
        declarations[source_id] = task
    for task in normal:
        if task.capability is None:
            raise ValueError(f"saga capability node '{task.id}' is not pinned")
        definition = definitions.get(task.capability.identity) or {}
        side_effect = str(definition.get("side_effect") or "none")
        if side_effect in {"none", "read"}:
            if task.id in declarations:
                raise ValueError(f"read-only saga source '{task.id}' cannot be compensated")
            continue
        if task.id not in declarations:
            raise ValueError(f"saga side-effect node '{task.id}' requires compensation")
    undeclared_sources = set(declarations) - {task.id for task in normal}
    if undeclared_sources:
        raise ValueError(f"saga compensation sources are invalid: {sorted(undeclared_sources)}")
