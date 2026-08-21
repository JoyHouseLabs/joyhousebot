"""Stable public EntryPoint projection and structured-input validation."""

from __future__ import annotations

import json
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for

_PUBLIC_ID_SEPARATOR = ":"


def normalize_entrypoint_input_schema(value: Any) -> dict[str, Any]:
    schema = dict(value or {"type": "object"})
    if schema.get("type", "object") != "object":
        raise ValueError("App entrypoint input_schema must describe an object")
    try:
        validator_for(schema).check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"App entrypoint input_schema is invalid: {exc.message}") from exc
    return schema


def validate_entrypoint_input(schema: dict[str, Any], value: dict[str, Any]) -> None:
    validator = validator_for(schema)(schema)
    errors = sorted(validator.iter_errors(value), key=lambda item: list(item.path))
    if not errors:
        return
    first = errors[0]
    path = ".".join(str(item) for item in first.absolute_path)
    location = f" at input.{path}" if path else ""
    raise ValueError(f"EntryPoint input is invalid{location}: {first.message}")


def public_entrypoint_id(installation_id: str, entrypoint_id: str) -> str:
    installation = str(installation_id).strip()
    local = str(entrypoint_id).strip()
    if not installation or not local or _PUBLIC_ID_SEPARATOR in installation:
        raise ValueError("EntryPoint installation and local identifiers are invalid")
    return f"{installation}{_PUBLIC_ID_SEPARATOR}{local}"


def split_public_entrypoint_id(value: str) -> tuple[str, str]:
    installation_id, separator, entrypoint_id = str(value).partition(_PUBLIC_ID_SEPARATOR)
    if not separator or not installation_id or not entrypoint_id:
        raise ValueError("public EntryPoint id is invalid")
    return installation_id, entrypoint_id


def entrypoint_descriptor(
    installation: dict[str, Any], entrypoint: dict[str, Any]
) -> dict[str, Any]:
    return {
        "id": public_entrypoint_id(
            str(installation["installation_id"]),
            str(entrypoint["entrypoint_id"]),
        ),
        "key": str(entrypoint["entrypoint_id"]),
        "app_id": str(installation["app_id"]),
        "name": str(entrypoint.get("name") or entrypoint["entrypoint_id"]),
        "description": str(entrypoint.get("description") or ""),
        "input_schema": normalize_entrypoint_input_schema(entrypoint.get("input_schema")),
        "output_schema": entrypoint.get("output_schema"),
        "interaction_mode": str(entrypoint.get("interaction_mode") or "auto"),
        "permission_summary": list(installation.get("granted_permissions") or []),
        "risk_summary": [],
    }


def structured_input_text(value: dict[str, Any]) -> str:
    """Translate structured public input to the existing internal prompt authority."""
    if set(value) == {"content"} and isinstance(value.get("content"), str):
        content = str(value["content"]).strip()
        if content:
            return content
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "entrypoint_descriptor",
    "normalize_entrypoint_input_schema",
    "public_entrypoint_id",
    "split_public_entrypoint_id",
    "structured_input_text",
    "validate_entrypoint_input",
]
