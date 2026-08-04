"""Pure validation and identity helpers for durable task graphs."""

from __future__ import annotations

import re
from typing import Any

from joyhousebot.runtime.models import GraphTaskSpec


def validate_and_order_graph(tasks: list[GraphTaskSpec]) -> list[GraphTaskSpec]:
    if not tasks:
        raise ValueError("task graph requires at least one task")
    if len(tasks) > 128:
        raise ValueError("task graph exceeds the 128 task limit")
    task_map = {task.id: task for task in tasks}
    if len(task_map) != len(tasks):
        raise ValueError("task ids must be unique")
    for task in tasks:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", task.id):
            raise ValueError(f"invalid task id: {task.id}")
        if not task.prompt.strip() and not task.capability:
            raise ValueError(f"task '{task.id}' prompt or capability is required")
        unknown = set(task.dependencies) - set(task_map)
        if unknown:
            raise ValueError(f"task '{task.id}' has unknown dependencies: {sorted(unknown)}")
        if task.id in task.dependencies:
            raise ValueError(f"task '{task.id}' cannot depend on itself")
    ordered: list[GraphTaskSpec] = []
    remaining = set(task_map)
    completed: set[str] = set()
    while remaining:
        ready = sorted(
            (
                task_map[task_id]
                for task_id in remaining
                if set(task_map[task_id].dependencies) <= completed
            ),
            key=lambda item: item.id,
        )
        if not ready:
            raise ValueError("task graph contains a cycle")
        ordered.extend(ready)
        ready_ids = {task.id for task in ready}
        completed.update(ready_ids)
        remaining -= ready_ids
    return ordered


def graph_task_id(run_id: str, spec_id: str) -> str:
    return f"{run_id}:{spec_id}"


def render_value(value: Any, variables: dict[str, Any]) -> Any:
    """Render exact ``${name}`` references without evaluating expressions."""
    if isinstance(value, dict):
        return {key: render_value(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [render_value(item, variables) for item in value]
    if isinstance(value, str):
        match = re.fullmatch(r"\$\{([A-Za-z0-9_.-]+)\}", value)
        if match:
            return variables.get(match.group(1), value)
        return re.sub(
            r"\$\{([A-Za-z0-9_.-]+)\}",
            lambda found: str(variables.get(found.group(1), found.group(0))),
            value,
        )
    return value
