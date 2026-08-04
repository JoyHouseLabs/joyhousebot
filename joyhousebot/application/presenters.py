"""Canonical JSON projections shared by HTTP and background integrations."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any


def record_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"unsupported record type: {type(value).__name__}")


def public_capability_definition(value: dict[str, Any]) -> dict[str, Any]:
    """Project catalog metadata without adapter-private configuration.

    Configuration *schema* is safe metadata and lets operators render a
    validated settings editor. Actual values stay behind the dedicated admin
    runtime-settings endpoint.
    """

    result = dict(value)
    result.pop("configuration", None)
    return result
