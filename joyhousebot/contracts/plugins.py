"""Business-neutral plugin catalog and registration contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PluginComponent:
    """One versioned business component contributed by a plugin."""

    component_id: str
    component_type: str
    name: str
    reference_id: str = ""
    reference_version: str = ""
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Safe, durable description of an installed plugin release."""

    plugin_id: str
    version: str
    name: str
    description: str = ""
    distribution_name: str = ""
    build_digest: str = ""
    runtime_contract_version: int = 1
    dependencies: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.plugin_id.strip() or not self.version.strip() or not self.name.strip():
            raise ValueError("plugin manifest id, version and name are required")
        if self.runtime_contract_version < 1:
            raise ValueError("plugin runtime_contract_version must be positive")
        for dependency in self.dependencies:
            if not str(dependency.get("id") or "").strip():
                raise ValueError("plugin dependency id is required")
            if str(dependency.get("kind") or "") not in {
                "database", "http", "queue", "object_store", "credential", "service",
            }:
                raise ValueError("plugin dependency kind is invalid")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["dependencies"] = list(self.dependencies)
        return value


@dataclass(frozen=True, slots=True)
class PluginHealthResult:
    """Safe result of a read-only plugin health check."""

    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"healthy", "degraded", "failed"}:
            raise ValueError("plugin health status must be healthy, degraded, or failed")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PluginHealthCheck:
    """A safe, explicitly triggered diagnostic supplied by a plugin."""

    name: str
    description: str
    run: Callable[[Any], Awaitable[PluginHealthResult] | PluginHealthResult]


@dataclass(frozen=True, slots=True)
class PluginHealthContext:
    """Opaque framework services exposed to a diagnostic implementation."""

    store: Any
    config: Any
    worker_id: str | None = None


@runtime_checkable
class PluginRegistry(Protocol):
    def register_capability(self, definition: Any, handler: Any) -> None:
        """Register a versioned capability and its handler."""


@runtime_checkable
class Plugin(Protocol):
    plugin_id: str
    version: str

    def register(self, registry: PluginRegistry) -> None:
        """Register the plugin's executable capabilities."""

    def manifest(self) -> PluginManifest:
        """Return safe release metadata for the platform control plane."""

    def health_checks(self) -> tuple[PluginHealthCheck, ...]:
        """Return opt-in read-only diagnostics; never expose secrets."""
