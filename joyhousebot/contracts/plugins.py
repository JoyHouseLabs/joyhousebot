"""Business-neutral plugin catalog and registration contracts."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from joyhousebot.contracts.extensions import ExtensionManifest

PLUGIN_COMPONENT_TYPES = frozenset(
    {
        "agent",
        "channel",
        "connector",
        "event_trigger",
        "knowledge_provider",
        "mcp_server",
        "scenario",
        "skill",
        "tool",
        "workflow",
    }
)


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

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,192}", self.component_id):
            raise ValueError("plugin component id is invalid")
        if self.component_type not in PLUGIN_COMPONENT_TYPES:
            raise ValueError("plugin component type is invalid")
        if not self.name.strip():
            raise ValueError("plugin component name is required")
        if self.reference_id and not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,192}", self.reference_id
        ):
            raise ValueError("plugin component reference id is invalid")
        if self.reference_id and not self.reference_version.strip():
            raise ValueError("plugin component reference version is required")
        if not isinstance(self.metadata, dict):
            raise ValueError("plugin component metadata must be an object")

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
    agent_id: str = "default"
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
    build_digest: str
    description: str = ""
    distribution_name: str = ""
    runtime_contract_version: int = 1
    runtime_api_version: str = "v1"
    execution_isolation: str = "in_process"
    default_agent_id: str = "default"
    required_permissions: tuple[str, ...] = ()
    package_uri: str = ""
    signature: str = ""
    signing_key_id: str = ""
    sbom_uri: str = ""
    dependencies: tuple[dict[str, Any], ...] = ()
    quickstarts: tuple[PluginQuickstart, ...] = ()

    def __post_init__(self) -> None:
        if not self.plugin_id.strip() or not self.version.strip() or not self.name.strip():
            raise ValueError("plugin manifest id, version and name are required")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.build_digest):
            raise ValueError("plugin manifest build_digest must be a sha256 digest")
        if self.runtime_contract_version < 1:
            raise ValueError("plugin runtime_contract_version must be positive")
        if self.runtime_api_version != "v1":
            raise ValueError("unsupported plugin runtime_api_version")
        if self.execution_isolation not in {"in_process", "container", "mcp"}:
            raise ValueError("plugin execution_isolation is invalid")
        if self.signature and not self.signing_key_id:
            raise ValueError("signed plugin manifests require signing_key_id")
        if any(not str(item).strip() for item in self.required_permissions):
            raise ValueError("plugin required_permissions must be non-empty")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.default_agent_id):
            raise ValueError("plugin default_agent_id is invalid")
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
        value["required_permissions"] = list(self.required_permissions)
        value["quickstarts"] = [item.to_dict() for item in self.quickstarts]
        value["extension_types"] = ["capability"]
        value["sdk_version"] = "1"
        return value

    def to_extension_manifest(self) -> ExtensionManifest:
        """Project capability-specific metadata onto the common extension identity."""
        return ExtensionManifest(
            extension_id=self.plugin_id,
            version=self.version,
            name=self.name,
            extension_types=("capability",),
            build_digest=self.build_digest,
            description=self.description,
            distribution_name=self.distribution_name,
            runtime_api_version=self.runtime_api_version,
            execution_isolation=self.execution_isolation,
            required_permissions=self.required_permissions,
            dependencies=self.dependencies,
        )

    def to_release_dict(self) -> dict[str, Any]:
        return self.to_dict()

    @property
    def extension_id(self) -> str:
        return self.plugin_id

    @property
    def extension_types(self) -> tuple[str, ...]:
        return ("capability",)

    @property
    def worker_capability(self) -> str:
        return "agent"


@runtime_checkable
class PluginRegistry(Protocol):
    def register_capability(self, definition: Any, handler: Any) -> None:
        """Register a versioned capability and its handler."""

    def register_component(
        self, component: PluginComponent, provider: Any | None = None
    ) -> None:
        """Register Scenario/Workflow/Agent/MCP/etc. metadata and provider."""


@runtime_checkable
class Plugin(Protocol):
    plugin_id: str
    version: str

    def register(self, registry: PluginRegistry) -> None:
        """Register the plugin's executable capabilities."""

    def manifest(self) -> PluginManifest:
        """Return safe release metadata for the platform control plane."""
