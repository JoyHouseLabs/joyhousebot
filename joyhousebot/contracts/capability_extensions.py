"""Capability Extension registration contracts.

This module contains only the trusted in-process capability-extension seam.
Product assets such as Agent, Workflow, Scenario, Skill, Prompt, and Quickstart
are published through the Build/App control plane and cannot be registered by
an Extension.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Protocol, runtime_checkable

from joyhousebot.contracts.extensions import ExtensionManifest


@dataclass(frozen=True, slots=True)
class CapabilityExtensionManifest:
    """Immutable identity and policy for one capability Extension release."""

    extension_id: str
    version: str
    name: str
    build_digest: str
    description: str = ""
    distribution_name: str = ""
    runtime_contract_version: int = 1
    runtime_api_version: str = "v1"
    execution_isolation: str = "in_process"
    required_permissions: tuple[str, ...] = ()
    package_uri: str = ""
    signature: str = ""
    signing_key_id: str = ""
    sbom_uri: str = ""
    dependencies: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.extension_id):
            raise ValueError("capability Extension id is invalid")
        if not self.version.strip() or not self.name.strip():
            raise ValueError("capability Extension version and name are required")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.build_digest):
            raise ValueError("capability Extension build_digest must be sha256")
        if self.runtime_contract_version < 1:
            raise ValueError("runtime_contract_version must be positive")
        if self.runtime_api_version != "v1":
            raise ValueError("unsupported capability Extension runtime_api_version")
        if self.execution_isolation not in {"in_process", "subprocess", "container", "mcp"}:
            raise ValueError("capability Extension execution_isolation is invalid")
        if self.signature and not self.signing_key_id:
            raise ValueError("signed capability Extension requires signing_key_id")
        if any(not str(item).strip() for item in self.required_permissions):
            raise ValueError("capability Extension permissions must be non-empty")
        for dependency in self.dependencies:
            if not str(dependency.get("id") or "").strip():
                raise ValueError("capability Extension dependency id is required")
            if str(dependency.get("kind") or "") not in {
                "credential", "database", "http", "object_store", "queue", "service",
            }:
                raise ValueError("capability Extension dependency kind is invalid")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["dependencies"] = list(self.dependencies)
        value["required_permissions"] = list(self.required_permissions)
        value["extension_types"] = ["capability"]
        value["sdk_version"] = "1"
        return value

    def to_extension_manifest(self) -> ExtensionManifest:
        return ExtensionManifest(
            extension_id=self.extension_id,
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
        """Return the durable Extension release representation."""
        return self.to_dict()

    @property
    def worker_capability(self) -> str:
        return "agent"


@runtime_checkable
class CapabilityExtensionRegistrar(Protocol):
    def register_capability(self, definition: Any, handler: Any) -> None:
        """Register one versioned Capability and its handler."""


@runtime_checkable
class CapabilityExtension(Protocol):
    extension_id: str
    version: str

    def register(self, registrar: CapabilityExtensionRegistrar) -> None:
        """Register only executable Capability definitions."""

    def manifest(self) -> CapabilityExtensionManifest:
        """Return immutable Extension release metadata."""


__all__ = [
    "CapabilityExtension",
    "CapabilityExtensionManifest",
    "CapabilityExtensionRegistrar",
]
