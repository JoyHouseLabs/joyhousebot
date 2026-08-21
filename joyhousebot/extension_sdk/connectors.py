"""Stable lifecycle and registration API for Capability Connector extensions."""

from typing import Any, Protocol

from joyhousebot.contracts.extensions import (
    CapabilityConnectorConnectRequest,
    CapabilityConnectorExtension,
)
from joyhousebot.domain.capabilities import CapabilityDefinition

from .tools import Tool


class CapabilityRegistrar(Protocol):
    """Narrow registration surface exposed by Core to a connector."""

    def register_connector_capability(
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
    "CapabilityConnectorConnectRequest",
    "CapabilityConnectorExtension",
    "CapabilityRegistrar",
    "connector_settings",
]
