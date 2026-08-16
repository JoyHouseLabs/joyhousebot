"""Safe, deterministic conditional routing for Graph branch nodes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_SOURCE = re.compile(r"^tasks\.([A-Za-z0-9_.-]{1,128})$")
_PATH = re.compile(r"^[A-Za-z0-9_.-]{1,512}$")
_OPERATORS = {"eq", "ne", "in", "not_in", "exists", "truthy", "contains"}
_MISSING = object()


@dataclass(frozen=True, slots=True)
class BranchDecision:
    source: str
    operator: str
    selected_targets: tuple[str, ...]
    matched_case: int | None
    used_default: bool


def branch_targets(configuration: dict[str, Any]) -> set[str]:
    targets = {str(item) for item in configuration.get("default_targets") or []}
    for case in configuration.get("cases") or []:
        if isinstance(case, dict):
            targets.update(str(item) for item in case.get("targets") or [])
    return targets


def validate_branch_configuration(
    node_id: str,
    configuration: dict[str, Any],
    *,
    dependencies: set[str],
    known_nodes: set[str],
    downstream_dependencies: dict[str, set[str]],
    node_specs: dict[str, Any],
) -> None:
    source = str(configuration.get("source") or "")
    path = str(configuration.get("path") or "")
    match = _SOURCE.fullmatch(source)
    if match is None or match.group(1) not in dependencies:
        raise ValueError(
            f"branch '{node_id}' source must reference one of its dependency task results"
        )
    if path and _PATH.fullmatch(path) is None:
        raise ValueError(f"branch '{node_id}' has an invalid result path")
    if path != "structured_output" and not path.startswith("structured_output."):
        raise ValueError(f"branch '{node_id}' must select from verified structured_output")
    source_spec = node_specs[match.group(1)]
    if getattr(source_spec, "output_schema", None) is None and getattr(
        source_spec, "node_type", None
    ) not in {"foreach", "wait_event", "verify", "bounded_loop", "aggregate"}:
        raise ValueError(f"branch '{node_id}' source task must declare output_schema")
    cases = configuration.get("cases") or []
    if not isinstance(cases, list) or len(cases) > 32:
        raise ValueError(f"branch '{node_id}' cases must be a list with at most 32 items")
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not isinstance(case.get("when"), dict):
            raise ValueError(f"branch '{node_id}' case {index} requires a when object")
        operator = str(case["when"].get("op") or "")
        if operator not in _OPERATORS:
            raise ValueError(f"branch '{node_id}' case {index} has an unsafe operator")
        targets = case.get("targets") or []
        if not isinstance(targets, list) or not targets:
            raise ValueError(f"branch '{node_id}' case {index} requires targets")
    defaults = configuration.get("default_targets")
    if not isinstance(defaults, list):
        raise ValueError(f"branch '{node_id}' requires default_targets")
    targets = branch_targets(configuration)
    if len(targets) > 64:
        raise ValueError(f"branch '{node_id}' exceeds the 64 target limit")
    unknown = targets - known_nodes
    if unknown:
        raise ValueError(f"branch '{node_id}' has unknown targets: {sorted(unknown)}")
    if node_id in targets:
        raise ValueError(f"branch '{node_id}' cannot target itself")
    missing_edges = {target for target in targets if node_id not in downstream_dependencies[target]}
    if missing_edges:
        raise ValueError(
            f"branch '{node_id}' targets must depend on the branch: {sorted(missing_edges)}"
        )
    undeclared = {
        target
        for target, target_dependencies in downstream_dependencies.items()
        if node_id in target_dependencies and target not in targets
    }
    if undeclared:
        raise ValueError(
            f"branch '{node_id}' has undeclared outgoing targets: {sorted(undeclared)}"
        )


def _read_source(source: str, path: str, results: dict[str, dict[str, Any]]) -> Any:
    match = _SOURCE.fullmatch(source)
    if match is None:
        return _MISSING
    value: Any = results.get(match.group(1), _MISSING)
    if value is _MISSING or not path:
        return value
    for part in path.split(".")[:16]:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return _MISSING
    return value


def _matches(value: Any, condition: dict[str, Any]) -> bool:
    operator = str(condition.get("op") or "")
    expected = condition.get("value")
    if operator == "exists":
        return (value is not _MISSING) is bool(condition.get("value", True))
    if value is _MISSING:
        return False
    if operator == "eq":
        return value == expected
    if operator == "ne":
        return value != expected
    if operator == "in":
        return isinstance(expected, list) and value in expected
    if operator == "not_in":
        return isinstance(expected, list) and value not in expected
    if operator == "truthy":
        return bool(value) is bool(condition.get("value", True))
    if operator == "contains":
        if isinstance(value, dict):
            return isinstance(expected, str) and expected in value
        if isinstance(value, str):
            return isinstance(expected, str) and expected in value
        if isinstance(value, list):
            return expected in value
    return False


def evaluate_branch(
    configuration: dict[str, Any], results: dict[str, dict[str, Any]]
) -> BranchDecision:
    """Evaluate the first matching case; arbitrary expressions are never executed."""
    source = str(configuration["source"])
    path = str(configuration.get("path") or "")
    value = _read_source(source, path, results)
    for index, case in enumerate(configuration.get("cases") or []):
        condition = dict(case["when"])
        if _matches(value, condition):
            return BranchDecision(
                source=source,
                operator=str(condition["op"]),
                selected_targets=tuple(dict.fromkeys(str(item) for item in case["targets"])),
                matched_case=index,
                used_default=False,
            )
    return BranchDecision(
        source=source,
        operator="default",
        selected_targets=tuple(
            dict.fromkeys(str(item) for item in configuration.get("default_targets") or [])
        ),
        matched_case=None,
        used_default=True,
    )
