"""Validation and result selection for bounded Graph ``foreach`` nodes."""

from __future__ import annotations

import re
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for

from joyhousebot.domain.capabilities import CapabilityRef

_SOURCE = re.compile(r"^tasks\.([A-Za-z0-9_.-]{1,128})$")
_PATH = re.compile(r"^[A-Za-z0-9_.-]{1,512}$")
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
_CONFIGURATION_KEYS = {"source", "path", "max_items", "max_concurrent", "template"}
_MISSING = object()


def _has_verified_output(spec: Any) -> bool:
    return getattr(spec, "output_schema", None) is not None or getattr(spec, "node_type", None) in {
        "foreach",
        "bounded_loop",
        "wait_event",
        "verify",
    }


def foreach_template_capability(configuration: dict[str, Any]) -> CapabilityRef | None:
    template = configuration.get("template")
    if not isinstance(template, dict) or not template.get("capability"):
        return None
    try:
        return CapabilityRef.from_dict(dict(template["capability"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("foreach template requires a pinned CapabilityRef") from exc


def _check_schema(schema: Any, *, label: str) -> None:
    if schema is None:
        return
    if not isinstance(schema, dict):
        raise ValueError(f"{label} must be a JSON Schema object")
    try:
        validator_for(schema).check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"{label} is not a valid JSON Schema") from exc


def validate_foreach_configuration(
    node_id: str,
    configuration: dict[str, Any],
    *,
    dependencies: set[str],
    node_specs: dict[str, Any],
) -> None:
    unknown_configuration = set(configuration) - _CONFIGURATION_KEYS
    if unknown_configuration:
        raise ValueError(
            f"foreach '{node_id}' has unsupported fields: {sorted(unknown_configuration)}"
        )
    source = str(configuration.get("source") or "")
    path = str(configuration.get("path") or "")
    match = _SOURCE.fullmatch(source)
    if match is None or match.group(1) not in dependencies:
        raise ValueError(
            f"foreach '{node_id}' source must reference one of its dependency task results"
        )
    if _PATH.fullmatch(path) is None or (
        path != "structured_output" and not path.startswith("structured_output.")
    ):
        raise ValueError(f"foreach '{node_id}' must select from verified structured_output")
    if not _has_verified_output(node_specs[match.group(1)]):
        raise ValueError(f"foreach '{node_id}' source task must declare output_schema")
    max_items = configuration.get("max_items")
    max_concurrent = configuration.get("max_concurrent")
    if type(max_items) is not int or not 1 <= max_items <= 64:
        raise ValueError(f"foreach '{node_id}' max_items must be between 1 and 64")
    if (
        type(max_concurrent) is not int
        or not 1 <= max_concurrent <= 32
        or max_concurrent > max_items
    ):
        raise ValueError(f"foreach '{node_id}' max_concurrent must be between 1 and max_items")
    template = configuration.get("template")
    if not isinstance(template, dict):
        raise ValueError(f"foreach '{node_id}' requires a template object")
    unknown = set(template) - _TEMPLATE_KEYS
    if unknown:
        raise ValueError(f"foreach '{node_id}' template has unsupported fields: {sorted(unknown)}")
    for field_name in ("metadata", "capability_input", "verification_policy"):
        value = template.get(field_name, {})
        if not isinstance(value, dict):
            raise ValueError(f"foreach '{node_id}' template {field_name} must be an object")
    for field_name in ("allowed_tools", "skill_names"):
        value = template.get(field_name, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"foreach '{node_id}' template {field_name} must be a string array")
    capability = foreach_template_capability(configuration)
    node_type = str(template.get("node_type") or ("capability" if capability else "agent"))
    if node_type not in {"agent", "capability"}:
        raise ValueError(f"foreach '{node_id}' template node_type is unsupported")
    if node_type == "agent" and not str(template.get("prompt") or "").strip():
        raise ValueError(f"foreach '{node_id}' agent template requires prompt")
    if node_type == "capability" and capability is None:
        raise ValueError(f"foreach '{node_id}' capability template requires CapabilityRef")
    timeout = template.get("timeout_seconds", 300)
    attempts = template.get("max_attempts", 1)
    repairs = template.get("max_repairs")
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
        raise ValueError(f"foreach '{node_id}' template timeout_seconds is invalid")
    if type(attempts) is not int or not 1 <= attempts <= 20:
        raise ValueError(f"foreach '{node_id}' template max_attempts is invalid")
    if repairs is not None and (type(repairs) is not int or not 0 <= repairs <= 10):
        raise ValueError(f"foreach '{node_id}' template max_repairs is invalid")
    _check_schema(
        template.get("output_schema"), label=f"foreach '{node_id}' template output_schema"
    )


def select_foreach_items(
    configuration: dict[str, Any], results: dict[str, dict[str, Any]]
) -> list[Any]:
    match = _SOURCE.fullmatch(str(configuration["source"]))
    value: Any = results.get(match.group(1), _MISSING) if match else _MISSING
    for part in str(configuration["path"]).split(".")[:16]:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            value = _MISSING
            break
    if not isinstance(value, list):
        raise ValueError("foreach source result must be an array")
    max_items = int(configuration["max_items"])
    if len(value) > max_items:
        raise ValueError(
            f"foreach source produced {len(value)} items, exceeding max_items={max_items}"
        )
    return value
