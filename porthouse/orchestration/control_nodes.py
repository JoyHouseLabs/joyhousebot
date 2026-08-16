"""Validation helpers for explicit Graph approval, verification, and compensation."""

from __future__ import annotations

import re
from typing import Any

from porthouse.domain.capabilities import CapabilityRef
from porthouse.orchestration.verification_policy import normalize_verifiers

_SOURCE = re.compile(r"^tasks\.([A-Za-z0-9_.-]{1,128})$")
_APPROVAL_KEYS = {
    "title",
    "description",
    "required_role",
    "expires_in_seconds",
    "risk",
    "data_classification",
}
_VERIFY_KEYS = {"source"}
_COMPENSATION_KEYS = {"source"}


def _reject_unknown(node_id: str, kind: str, value: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{kind} '{node_id}' has unsupported fields: {sorted(unknown)}")


def _source_node(
    node_id: str,
    kind: str,
    configuration: dict[str, Any],
    dependencies: set[str],
) -> str:
    source = str(configuration.get("source") or "")
    match = _SOURCE.fullmatch(source)
    if match is None or match.group(1) not in dependencies:
        raise ValueError(f"{kind} '{node_id}' source must reference one of its dependency tasks")
    return match.group(1)


def validate_approval_node(task: Any) -> None:
    configuration = task.approval
    _reject_unknown(task.id, "approval", configuration, _APPROVAL_KEYS)
    if not 1 <= len(task.dependencies) <= 16:
        raise ValueError(f"approval '{task.id}' requires 1-16 dependency tasks")
    if task.max_attempts != 1:
        raise ValueError(f"approval '{task.id}' max_attempts must be 1")
    role = str(configuration.get("required_role") or "owner")
    if role not in {"owner", "operator"}:
        raise ValueError(f"approval '{task.id}' required_role is invalid")
    deadline = configuration.get("expires_in_seconds", 86_400)
    if type(deadline) is not int or not 1 <= deadline <= 604_800:
        raise ValueError(f"approval '{task.id}' expires_in_seconds must be between 1 and 604800")
    if str(configuration.get("risk") or "medium") not in {"low", "medium", "high"}:
        raise ValueError(f"approval '{task.id}' risk is invalid")
    if str(configuration.get("data_classification") or "internal") not in {
        "public",
        "internal",
        "confidential",
        "restricted",
    }:
        raise ValueError(f"approval '{task.id}' data_classification is invalid")
    if len(str(configuration.get("title") or task.name or task.id)) > 200:
        raise ValueError(f"approval '{task.id}' title is too long")
    if len(str(configuration.get("description") or task.prompt or "")) > 2000:
        raise ValueError(f"approval '{task.id}' description is too long")


def validate_verify_node(task: Any) -> None:
    _reject_unknown(task.id, "verify", task.verify, _VERIFY_KEYS)
    _source_node(task.id, "verify", task.verify, set(task.dependencies))
    if task.max_attempts != 1:
        raise ValueError(f"verify '{task.id}' max_attempts must be 1")
    if task.max_repairs not in {None, 0}:
        raise ValueError(f"verify '{task.id}' cannot repair an immutable source result")
    try:
        verifiers = normalize_verifiers(task.output_schema, task.verification_policy)
    except ValueError as exc:
        raise ValueError(f"verify '{task.id}' policy is invalid: {exc}") from exc
    if not verifiers:
        raise ValueError(f"verify '{task.id}' requires output_schema or verification_policy")


def validate_compensation_node(task: Any, node_specs: dict[str, Any]) -> None:
    _reject_unknown(task.id, "compensation", task.compensation, _COMPENSATION_KEYS)
    source_id = _source_node(task.id, "compensation", task.compensation, set(task.dependencies))
    if len(task.dependencies) != 1:
        raise ValueError(f"compensation '{task.id}' requires exactly one source dependency")
    if getattr(node_specs[source_id], "node_type", None) != "capability":
        raise ValueError(f"compensation '{task.id}' source must be a direct capability node")
    if task.capability is None:
        raise ValueError(f"compensation '{task.id}' requires a pinned CapabilityRef")
    if task.max_attempts != 1:
        raise ValueError(f"compensation '{task.id}' max_attempts must be 1")


def control_source_id(configuration: dict[str, Any]) -> str:
    match = _SOURCE.fullmatch(str(configuration.get("source") or ""))
    if match is None:
        raise ValueError("invalid Graph control-node source")
    return match.group(1)


def validate_compensation_declarations(tasks: list[Any], catalog: list[dict[str, Any]]) -> None:
    definitions = {CapabilityRef.from_dict(dict(item["ref"])).identity: item for item in catalog}
    task_map = {task.id: task for task in tasks}
    for task in tasks:
        if task.node_type != "compensation":
            continue
        source = task_map[control_source_id(task.compensation)]
        if source.capability is None or task.capability is None:
            raise ValueError(f"compensation '{task.id}' requires pinned capabilities")
        source_definition = definitions.get(source.capability.identity)
        try:
            declared = CapabilityRef.from_dict(
                dict((source_definition or {}).get("compensation") or {})
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"compensation '{task.id}' source capability declares no compensation"
            ) from exc
        if (
            declared.identity != task.capability.identity
            or task.capability.identity not in definitions
        ):
            raise ValueError(
                f"compensation '{task.id}' does not match the source capability declaration"
            )
