"""Model provider extension discovery and endpoint metadata."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from porthouse.contracts.extensions import ModelProviderExtension, ModelProviderSpec
from porthouse.extension_discovery import enabled_entry_points, validate_manifest

MODEL_PROVIDER_ENTRY_POINT_GROUP = "porthouse.model_providers"


class ModelProviderRegistry:
    def __init__(
        self,
        *,
        enabled: Iterable[str] = (),
        discover_entry_points: bool = True,
    ) -> None:
        self._specs: dict[str, ModelProviderSpec] = {}
        self._extensions: dict[str, ModelProviderExtension] = {}
        self._provider_extensions: dict[str, ModelProviderExtension] = {}
        self._sources: dict[str, str] = {}
        self._entry_points_loaded = False
        self._enabled = frozenset(str(item).strip() for item in enabled if str(item).strip())
        self._discover_entry_points = bool(discover_entry_points)

    def register(self, extension: ModelProviderExtension, *, source: str) -> None:
        extension_id = extension.manifest.extension_id
        existing = self._extensions.get(extension_id)
        if existing is not None:
            return
        for spec in extension.providers:
            owner = self._provider_extensions.get(spec.name)
            if owner is not None:
                raise ValueError(
                    f"model provider {spec.name!r} is already provided by "
                    f"{owner.manifest.extension_id}"
                )
            self._specs[spec.name] = spec
            self._provider_extensions[spec.name] = extension
            self._sources[spec.name] = source
        self._extensions[extension_id] = extension

    def load_entry_points(self) -> list[str]:
        if self._entry_points_loaded:
            return []
        self._entry_points_loaded = True
        if not self._discover_entry_points:
            return []
        loaded = []
        for entry in enabled_entry_points(MODEL_PROVIDER_ENTRY_POINT_GROUP, self._enabled):
            exported = entry.load()
            extension = self._extension_from_export(
                exported, source=f"entry-point:{entry.name}"
            )
            validate_manifest(
                extension.manifest,
                entry_name=str(entry.name),
                expected_type="model_provider",
            )
            self.register(extension, source=f"entry-point:{entry.name}")
            loaded.append(extension.manifest.extension_id)
        return loaded

    def ensure_provider(self, name: str) -> ModelProviderSpec | None:
        normalized = str(name).strip().lower()
        self.load_entry_points()
        return self._specs.get(normalized)

    def specs(self) -> tuple[ModelProviderSpec, ...]:
        self.load_entry_points()
        return tuple(self._specs.values())

    def extension_for(self, name: str) -> ModelProviderExtension | None:
        self.ensure_provider(name)
        return self._provider_extensions.get(str(name).strip().lower())

    def manifests(self) -> tuple[Any, ...]:
        self.load_entry_points()
        return tuple(extension.manifest for extension in self._extensions.values())

    def source_for(self, name: str) -> str | None:
        return self._sources.get(str(name).strip().lower())

    @staticmethod
    def _extension_from_export(exported: Any, *, source: str) -> ModelProviderExtension:
        factory = getattr(exported, "create_extension", None)
        if callable(factory):
            value = factory()
        elif callable(exported):
            value = exported()
        else:
            value = exported
        if not isinstance(value, ModelProviderExtension):
            raise TypeError(f"{source} did not return a ModelProviderExtension")
        return value


def get_provider_registry(config: Any) -> ModelProviderRegistry:
    registry = getattr(config, "_model_provider_registry", None)
    if registry is None:
        extensions = getattr(config, "extensions", None)
        explicit = getattr(extensions, "allowed_ids", ()) or ()
        legacy = getattr(extensions, "enabled", ()) or ()
        enabled = {
            str(item).strip()
            for item in (*explicit, *legacy)
            if str(item).strip().startswith("provider-")
        }
        registry = ModelProviderRegistry(
            enabled=enabled,
            discover_entry_points=bool(
                getattr(extensions, "discover_entry_points", True)
            ),
        )
        config._model_provider_registry = registry
    return registry


def provider_specs(config: Any) -> tuple[ModelProviderSpec, ...]:
    return get_provider_registry(config).specs()


def find_by_name(config: Any, name: str) -> ModelProviderSpec | None:
    return get_provider_registry(config).ensure_provider(name)


def find_by_model(config: Any, model: str) -> ModelProviderSpec | None:
    normalized = model.lower()
    for item in provider_specs(config):
        if item.is_gateway or item.is_local:
            continue
        if any(keyword in normalized for keyword in item.keywords):
            return item
    return None


__all__ = [
    "MODEL_PROVIDER_ENTRY_POINT_GROUP",
    "ModelProviderRegistry",
    "find_by_model",
    "find_by_name",
    "get_provider_registry",
    "provider_specs",
]
