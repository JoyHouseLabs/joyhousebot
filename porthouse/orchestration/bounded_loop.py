"""Validation and deterministic state selection for bounded Graph loops."""

from __future__ import annotations

import json
import re
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for

from porthouse.domain.capabilities import CapabilityRef

_SOURCE = re.compile(r"^tasks\.([A-Za-z0-9_.-]{1,128})$")
_PATH = re.compile(r"^[A-Za-z0-9_.-]{1,512}$")
_OPERATORS = {"eq", "ne", "in", "not_in", "exists", "truthy", "contains"}
_CONFIGURATION_KEYS = {
    "source",
    "path",
    "initial_state",
    "state_path",
    "max_iterations",
    "exit",
    "template",
}
_EXIT_KEYS = {"path", "when"}
_CONDITION_KEYS = {"op", "value"}
_TEMPLATE_KEYS = {
    "node_type",
    "name",
    "agent_id",
    "prompt",
    "timeout_seconds",
    "max_attempts",
    "metadata",
    "capability",
    "capability_input",
    "output_schema",
    "verification_policy",
    "max_repairs",
    "allowed_tools",
    "skill_names",
}
_MISSING = object()


def _verified_output(spec: Any) -> bool:
    return getattr(spec, "output_schema", None) is not None or getattr(
        spec, "node_type", None
    ) in {"foreach", "wait_event", "verify", "bounded_loop"}


def _valid_verified_path(path: str) -> bool:
    return bool(
        _PATH.fullmatch(path)
        and (path == "structured_output" or path.startswith("structured_output."))
    )


def _check_schema(schema: Any, *, node_id: str) -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"bounded_loop '{node_id}' template requires output_schema")
    try:
        validator_for(schema).check_schema(schema)
    except SchemaError as exc:
        raise ValueError(
            f"bounded_loop '{node_id}' template output_schema is invalid"
        ) from exc


def bounded_loop_template_capability(
    configuration: dict[str, Any],
) -> CapabilityRef | None:
    template = configuration.get("template")
    if not isinstance(template, dict) or not template.get("capability"):
        return None
    try:
        return CapabilityRef.from_dict(dict(template["capability"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("bounded_loop template requires a pinned CapabilityRef") from exc


def validate_bounded_loop_configuration(
    node_id: str,
    configuration: dict[str, Any],
    *,
    dependencies: set[str],
    node_specs: dict[str, Any],
) -> None:
    unknown = set(configuration) - _CONFIGURATION_KEYS
    if unknown:
        raise ValueError(
            f"bounded_loop '{node_id}' has unsupported fields: {sorted(unknown)}"
        )
    source = str(configuration.get("source") or "")
    has_initial = "initial_state" in configuration
    if bool(source) == has_initial:
        raise ValueError(
            f"bounded_loop '{node_id}' requires exactly one of source or initial_state"
        )
    if source:
        match = _SOURCE.fullmatch(source)
        if match is None or match.group(1) not in dependencies:
            raise ValueError(
                f"bounded_loop '{node_id}' source must reference a dependency task result"
            )
        if not _valid_verified_path(str(configuration.get("path") or "")):
            raise ValueError(
                f"bounded_loop '{node_id}' source must select verified structured_output"
            )
        if not _verified_output(node_specs[match.group(1)]):
            raise ValueError(
                f"bounded_loop '{node_id}' source task must declare output_schema"
            )
    else:
        if configuration.get("path") is not None:
            raise ValueError(f"bounded_loop '{node_id}' path requires source")
        try:
            encoded = json.dumps(configuration["initial_state"], ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bounded_loop '{node_id}' initial_state must be JSON") from exc
        if len(encoded.encode("utf-8")) > 65_536:
            raise ValueError(f"bounded_loop '{node_id}' initial_state exceeds 64 KiB")
    if not _valid_verified_path(str(configuration.get("state_path") or "")):
        raise ValueError(
            f"bounded_loop '{node_id}' state_path must select verified structured_output"
        )
    maximum = configuration.get("max_iterations")
    if type(maximum) is not int or not 1 <= maximum <= 32:
        raise ValueError(
            f"bounded_loop '{node_id}' max_iterations must be between 1 and 32"
        )
    if getattr(node_specs[node_id], "max_attempts", 1) != 1:
        raise ValueError(f"bounded_loop '{node_id}' max_attempts must be 1")
    exit_rule = configuration.get("exit")
    if not isinstance(exit_rule, dict) or set(exit_rule) - _EXIT_KEYS:
        raise ValueError(f"bounded_loop '{node_id}' exit configuration is invalid")
    if not _valid_verified_path(str(exit_rule.get("path") or "")):
        raise ValueError(
            f"bounded_loop '{node_id}' exit path must select verified structured_output"
        )
    condition = exit_rule.get("when")
    if (
        not isinstance(condition, dict)
        or set(condition) - _CONDITION_KEYS
        or str(condition.get("op") or "") not in _OPERATORS
    ):
        raise ValueError(f"bounded_loop '{node_id}' exit condition has an unsafe operator")
    _validate_template(node_id, configuration)


def _validate_template(node_id: str, configuration: dict[str, Any]) -> None:
    template = configuration.get("template")
    if not isinstance(template, dict):
        raise ValueError(f"bounded_loop '{node_id}' requires a template object")
    unknown = set(template) - _TEMPLATE_KEYS
    if unknown:
        raise ValueError(
            f"bounded_loop '{node_id}' template has unsupported fields: {sorted(unknown)}"
        )
    capability = bounded_loop_template_capability(configuration)
    node_type = str(template.get("node_type") or ("capability" if capability else "agent"))
    if node_type not in {"agent", "capability"}:
        raise ValueError(f"bounded_loop '{node_id}' template node_type is unsupported")
    if node_type == "agent" and not str(template.get("prompt") or "").strip():
        raise ValueError(f"bounded_loop '{node_id}' agent template requires prompt")
    if node_type == "capability" and capability is None:
        raise ValueError(f"bounded_loop '{node_id}' capability template requires CapabilityRef")
    for field_name in ("metadata", "capability_input", "verification_policy"):
        if not isinstance(template.get(field_name, {}), dict):
            raise ValueError(
                f"bounded_loop '{node_id}' template {field_name} must be an object"
            )
    for field_name in ("allowed_tools", "skill_names"):
        value = template.get(field_name, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(
                f"bounded_loop '{node_id}' template {field_name} must be a string array"
            )
    timeout = template.get("timeout_seconds", 300)
    attempts = template.get("max_attempts", 1)
    repairs = template.get("max_repairs")
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
        raise ValueError(f"bounded_loop '{node_id}' template timeout_seconds is invalid")
    if type(attempts) is not int or not 1 <= attempts <= 20:
        raise ValueError(f"bounded_loop '{node_id}' template max_attempts is invalid")
    if repairs is not None and (type(repairs) is not int or not 0 <= repairs <= 10):
        raise ValueError(f"bounded_loop '{node_id}' template max_repairs is invalid")
    _check_schema(template.get("output_schema"), node_id=node_id)


def _read_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split(".")[:16]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return _MISSING
    return current


def select_initial_loop_state(
    configuration: dict[str, Any], results: dict[str, dict[str, Any]]
) -> Any:
    if "initial_state" in configuration:
        return configuration["initial_state"]
    match = _SOURCE.fullmatch(str(configuration["source"]))
    value = results.get(match.group(1), _MISSING) if match else _MISSING
    selected = _read_path(value, str(configuration["path"]))
    if selected is _MISSING:
        raise ValueError("bounded_loop initial state path is missing")
    return selected


def select_next_loop_state(configuration: dict[str, Any], result: dict[str, Any]) -> Any:
    selected = _read_path(result, str(configuration["state_path"]))
    if selected is _MISSING:
        raise ValueError("bounded_loop state_path is missing from iteration output")
    return selected


def loop_should_exit(configuration: dict[str, Any], result: dict[str, Any]) -> bool:
    exit_rule = dict(configuration["exit"])
    value = _read_path(result, str(exit_rule["path"]))
    condition = dict(exit_rule["when"])
    operator = str(condition["op"])
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
