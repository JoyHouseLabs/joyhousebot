"""Versioned contracts shared by every JoyhouseBot extension type."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

EXTENSION_SDK_VERSION = "1"
RUNTIME_API_VERSION = "v1"
EXTENSION_TYPES = frozenset(
    {
        "capability",
        "channel",
        "context_provider",
        "model_provider",
        "tool_connector",
        "ui",
    }
)


@dataclass(frozen=True, slots=True)
class ExtensionManifest:
    """Safe identity and compatibility metadata for one extension release."""

    extension_id: str
    version: str
    name: str
    extension_types: tuple[str, ...]
    build_digest: str
    description: str = ""
    distribution_name: str = ""
    runtime_api_version: str = RUNTIME_API_VERSION
    sdk_version: str = EXTENSION_SDK_VERSION
    execution_isolation: str = "in_process"
    required_permissions: tuple[str, ...] = ()
    dependencies: tuple[dict[str, Any], ...] = ()
    configuration_schema: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.extension_id):
            raise ValueError("extension manifest id is invalid")
        if not self.version.strip() or not self.name.strip():
            raise ValueError("extension manifest version and name are required")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.build_digest):
            raise ValueError("extension manifest build_digest must be a sha256 digest")
        if not self.extension_types or any(
            item not in EXTENSION_TYPES for item in self.extension_types
        ):
            raise ValueError("extension manifest type is invalid")
        if self.runtime_api_version != RUNTIME_API_VERSION:
            raise ValueError("unsupported extension runtime_api_version")
        if self.sdk_version != EXTENSION_SDK_VERSION:
            raise ValueError("unsupported extension sdk_version")
        if self.execution_isolation not in {"in_process", "container", "mcp"}:
            raise ValueError("extension execution_isolation is invalid")
        if any(not str(item).strip() for item in self.required_permissions):
            raise ValueError("extension required permissions must be non-empty")
        for dependency in self.dependencies:
            if not str(dependency.get("id") or "").strip():
                raise ValueError("extension dependency id is required")
            if str(dependency.get("kind") or "") not in {
                "credential",
                "database",
                "http",
                "object_store",
                "queue",
                "service",
            }:
                raise ValueError("extension dependency kind is invalid")
        if not isinstance(self.configuration_schema, dict):
            raise ValueError("extension configuration_schema must be an object")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["extension_types"] = list(self.extension_types)
        value["required_permissions"] = list(self.required_permissions)
        value["dependencies"] = list(self.dependencies)
        return value

    def to_release_dict(self) -> dict[str, Any]:
        """Return the durable release-catalog representation.

        The existing control-plane table uses ``plugin_id`` as its aggregate
        key. Every independently installed package now shares that catalog,
        so the value is always the exact extension id.
        """
        value = self.to_dict()
        value["plugin_id"] = value.pop("extension_id")
        return value

    @property
    def worker_capability(self) -> str | None:
        """Worker role that must acknowledge this release before activation."""
        types = set(self.extension_types)
        if "channel" in types:
            return "channels"
        if types & {"capability", "context_provider", "model_provider", "tool_connector"}:
            return "agent"
        return None


@dataclass(frozen=True, slots=True)
class ModelProviderSpec:
    """One model endpoint family contributed by a provider extension."""

    name: str
    keywords: tuple[str, ...]
    default_api_base: str = ""
    env_key: str = ""
    is_gateway: bool = False
    is_local: bool = False
    default_model: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", self.name):
            raise ValueError("model provider name is invalid")


@dataclass(frozen=True, slots=True)
class ModelProviderBuildRequest:
    """Provider-neutral construction input passed to an extension factory."""

    provider_name: str
    api_key: str
    api_base: str
    default_model: str
    extra_headers: dict[str, str] = field(default_factory=dict)
    reasoning_options: dict[str, Any] = field(default_factory=dict)
    request_timeout_seconds: float = 120.0
    client: Any = None


@dataclass(frozen=True, slots=True)
class ModelProviderExtension:
    """Installed model provider factory plus its endpoint metadata."""

    manifest: ExtensionManifest
    providers: tuple[ModelProviderSpec, ...]
    factory: Callable[[ModelProviderBuildRequest], Any]

    def __post_init__(self) -> None:
        if "model_provider" not in self.manifest.extension_types:
            raise ValueError("model provider extension manifest type is required")
        if not self.providers:
            raise ValueError("model provider extension must declare at least one provider")
        if len({item.name for item in self.providers}) != len(self.providers):
            raise ValueError("model provider extension names must be unique")
        if not callable(self.factory):
            raise TypeError("model provider extension factory must be callable")


@dataclass(frozen=True, slots=True)
class ToolConnectorConnectRequest:
    """Framework-owned resources passed to one configured Tool connector."""

    settings: dict[str, Any]
    registry: Any
    lifecycle: Any


@dataclass(frozen=True, slots=True)
class ToolConnectorExtension:
    """Installed connector that contributes model-facing Tools at worker startup."""

    manifest: ExtensionManifest
    connect: Callable[[ToolConnectorConnectRequest], Awaitable[None]]

    def __post_init__(self) -> None:
        if "tool_connector" not in self.manifest.extension_types:
            raise ValueError("tool connector extension manifest type is required")
        if not callable(self.connect):
            raise TypeError("tool connector extension connect callback is required")


__all__ = [
    "EXTENSION_SDK_VERSION",
    "EXTENSION_TYPES",
    "ModelProviderBuildRequest",
    "ModelProviderExtension",
    "ModelProviderSpec",
    "RUNTIME_API_VERSION",
    "ToolConnectorConnectRequest",
    "ToolConnectorExtension",
    "ExtensionManifest",
]
