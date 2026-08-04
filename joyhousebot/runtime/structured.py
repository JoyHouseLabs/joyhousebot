"""Standards-compliant structured Agent output parsing and validation."""

from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import SchemaError
from jsonschema.validators import validator_for


class StructuredOutputError(ValueError):
    """Raised when an Agent response is not valid structured output."""


def parse_structured_output(content: str | None, schema: dict[str, Any]) -> Any:
    raw = (content or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise StructuredOutputError("agent response is not valid JSON") from exc
    errors = validate_json_schema(value, schema)
    if errors:
        raise StructuredOutputError("structured output validation failed: " + "; ".join(errors))
    return value


def _json_path(parts: list[Any]) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def validate_json_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate with the JSON Schema dialect declared by the schema.

    The optional ``path`` prefix is retained for callers validating nested
    fragments. Schema errors are returned as validation errors so a malformed
    administrator definition fails closed instead of bypassing validation.
    """

    validator_class = validator_for(schema)
    try:
        validator_class.check_schema(schema)
    except SchemaError as exc:
        return [f"{path} schema is invalid: {exc.message}"]
    validator = validator_class(schema)
    errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    return [
        f"{path}{_json_path(list(error.absolute_path))[1:]} {error.message}"
        for error in errors
    ]
