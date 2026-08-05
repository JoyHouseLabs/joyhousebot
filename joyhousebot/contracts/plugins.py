"""Business-neutral plugin catalog and registration contracts."""

from __future__ import annotations

import re
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
class PluginQuickstart:
    """A versioned, business-owned example that enters the normal Run path.

    The framework renders and executes this metadata generically; a plugin
    owns the business wording, suggested request and expected capabilities.
    Quickstarts are deliberately prompts rather than hidden tool calls, so
    the coordinator, policy checks, scenario routing and full audit trail all
    remain in the execution path.
    """

    quickstart_id: str
    title: str
    description: str
    prompt: str
    agent_id: str = "main-coordinator"
    scenario_id: str | None = None
    scenario_inputs: dict[str, Any] = field(default_factory=dict)
    capability_ids: tuple[str, ...] = ()
    required_connection_ids: tuple[str, ...] = ()
    expected_outcome: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.quickstart_id):
            raise ValueError("plugin quickstart id is invalid")
        if not self.title.strip() or not self.description.strip() or not self.prompt.strip():
            raise ValueError("plugin quickstart title, description, and prompt are required")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.agent_id):
            raise ValueError("plugin quickstart agent_id is invalid")
        if self.scenario_id and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.scenario_id):
            raise ValueError("plugin quickstart scenario_id is invalid")
        if not isinstance(self.scenario_inputs, dict):
            raise ValueError("plugin quickstart scenario_inputs must be an object")
        if any(not item.strip() for item in (*self.capability_ids, *self.required_connection_ids)):
            raise ValueError("plugin quickstart references must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["capability_ids"] = list(self.capability_ids)
        value["required_connection_ids"] = list(self.required_connection_ids)
        return value


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
    quickstarts: tuple[PluginQuickstart, ...] = ()

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
        quickstart_ids = [item.quickstart_id for item in self.quickstarts]
        if len(quickstart_ids) != len(set(quickstart_ids)):
            raise ValueError("plugin quickstart ids must be unique")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["dependencies"] = list(self.dependencies)
        value["quickstarts"] = [item.to_dict() for item in self.quickstarts]
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
