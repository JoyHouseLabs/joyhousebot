"""Stable lifecycle and registration API for Tool connector extensions."""

from typing import Any, Protocol

from porthouse.contracts.extensions import (
    ToolConnectorConnectRequest,
    ToolConnectorExtension,
)
from porthouse.domain.capabilities import CapabilityDefinition

from .tools import Tool


class ToolRegistrar(Protocol):
    """Narrow registration surface exposed by Core to a connector."""

    def register_tool(
        self,
        tool: Tool,
        *,
        definition: CapabilityDefinition,
        optional: bool = False,
    ) -> None: ...


def connector_settings(value: Any) -> dict[str, Any]:
    """Normalize validated mappings and Pydantic compatibility values."""
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dict(dump())
    raise TypeError("connector settings must be an object")


__all__ = [
    "ToolConnectorConnectRequest",
    "ToolConnectorExtension",
    "ToolRegistrar",
    "connector_settings",
]
