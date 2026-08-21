"""Generic discovery for Extensions that attach external Capability catalogs."""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from typing import Any

from joyhousebot.contracts.extensions import (
    CapabilityConnectorConnectRequest,
    CapabilityConnectorExtension,
)
from joyhousebot.extension_discovery import allowed_entry_points, validate_manifest

CAPABILITY_CONNECTOR_ENTRY_POINT_GROUP = "joyhousebot.capability_connectors"


class CapabilityConnectorRegistry:
    """Own installed connector discovery without knowing any vendor protocol."""

    def __init__(self) -> None:
        self._extensions: dict[str, CapabilityConnectorExtension] = {}
        self._sources: dict[str, str] = {}
        self._entry_points_loaded = False

    def register(self, extension: CapabilityConnectorExtension, *, source: str) -> None:
        if not isinstance(extension, CapabilityConnectorExtension):
            raise TypeError(f"{source} did not return a CapabilityConnectorExtension")
        extension_id = extension.manifest.extension_id
        existing = self._extensions.get(extension_id)
        if existing is not None and existing is not extension:
            raise ValueError(
                f"Capability Connector {extension_id!r} is already provided by "
                f"{self._sources[extension_id]}"
            )
        self._extensions[extension_id] = extension
        self._sources[extension_id] = source

    def get(self, extension_id: str) -> CapabilityConnectorExtension | None:
        return self._extensions.get(str(extension_id).strip())

    def load_entry_points(self, *, allowed_ids: Iterable[str]) -> list[str]:
        if self._entry_points_loaded:
            return []
        self._entry_points_loaded = True
        loaded: list[str] = []
        for entry in allowed_entry_points(
            CAPABILITY_CONNECTOR_ENTRY_POINT_GROUP, allowed_ids
        ):
            extension = self._extension_from_export(
                entry.load(), source=f"entry-point:{entry.name}"
            )
            validate_manifest(
                extension.manifest,
                entry_name=str(entry.name),
                expected_type="connector",
            )
            self.register(extension, source=f"entry-point:{entry.name}")
            loaded.append(extension.manifest.extension_id)
        return loaded

    def manifests(self) -> tuple[Any, ...]:
        return tuple(extension.manifest for extension in self._extensions.values())

    async def connect_configured(
        self,
        settings: dict[str, dict[str, Any]],
        *,
        capability_registry: Any,
        lifecycle: Any,
    ) -> None:
        for extension_id, configuration in settings.items():
            extension = self.get(extension_id)
            if extension is None:
                raise RuntimeError(
                    f"Capability Connector {extension_id!r} is enabled but no installed "
                    "extension provides it"
                )
            result = extension.connect(
                CapabilityConnectorConnectRequest(
                    settings=dict(configuration),
                    registry=capability_registry,
                    lifecycle=lifecycle,
                )
            )
            if inspect.isawaitable(result):
                await result

    @staticmethod
    def _extension_from_export(exported: Any, *, source: str) -> CapabilityConnectorExtension:
        factory = getattr(exported, "create_extension", None)
        if callable(factory):
            value = factory()
        elif callable(exported):
            value = exported()
        else:
            value = exported
        if not isinstance(value, CapabilityConnectorExtension):
            raise TypeError(f"{source} did not return a CapabilityConnectorExtension")
        return value


__all__ = [
    "CAPABILITY_CONNECTOR_ENTRY_POINT_GROUP",
    "CapabilityConnectorRegistry",
]
