"""Validation helpers for durable Graph external-event waits."""

from __future__ import annotations

import re
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for

_EVENT_TYPE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_CONFIGURATION_KEYS = {"event_type", "deadline_seconds", "payload_schema"}


def validate_wait_event_configuration(node_id: str, configuration: dict[str, Any]) -> None:
    unknown = set(configuration) - _CONFIGURATION_KEYS
    if unknown:
        raise ValueError(f"wait_event '{node_id}' has unsupported fields: {sorted(unknown)}")
    event_type = str(configuration.get("event_type") or "")
    if _EVENT_TYPE.fullmatch(event_type) is None:
        raise ValueError(f"wait_event '{node_id}' event_type is invalid")
    deadline_seconds = configuration.get("deadline_seconds")
    if type(deadline_seconds) is not int or not 1 <= deadline_seconds <= 604800:
        raise ValueError(f"wait_event '{node_id}' deadline_seconds must be between 1 and 604800")
    schema = configuration.get("payload_schema")
    if not isinstance(schema, dict):
        raise ValueError(f"wait_event '{node_id}' requires payload_schema")
    try:
        validator_for(schema).check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"wait_event '{node_id}' payload_schema is invalid") from exc
