"""Serializable Extension ABI types with no Runtime dependency."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

SDK_VERSION = "2"
HOST_PROTOCOL_VERSION = "1"
EXTENSION_TYPES = frozenset(
    {"capability", "channel", "connector", "context_provider", "host_integration", "provider"}
)


class CapabilityKind(StrEnum):
    CAPABILITY = "capability"
    CONNECTOR = "connector"


@dataclass(frozen=True, slots=True)
class CapabilityRef:
    capability_id: str
    version: str
    implementation_digest: str
    kind: CapabilityKind = CapabilityKind.CAPABILITY

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", self.capability_id):
            raise ValueError("capability_id is invalid")
        if not self.version.strip():
            raise ValueError("capability version is required")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.implementation_digest):
            raise ValueError("implementation_digest must be sha256")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "kind": self.kind.value}


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    ref: CapabilityRef
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permissions: tuple[str, ...] = ()
    side_effect: Literal["none", "idempotent", "non_idempotent"] = "none"
    timeout_seconds: int = 60
    data_classification: Literal["public", "internal", "confidential", "restricted"] = "internal"

    def __post_init__(self) -> None:
        if not self.name.strip() or self.input_schema.get("type", "object") != "object":
            raise ValueError("capability name and object input_schema are required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ref": self.ref.to_dict(), "permissions": list(self.permissions)}


@dataclass(frozen=True, slots=True)
class ExtensionManifest:
    extension_id: str
    version: str
    name: str
    extension_types: tuple[str, ...]
    build_digest: str
    lockfile_digest: str
    description: str = ""
    sdk_version: str = SDK_VERSION
    host_protocol_version: str = HOST_PROTOCOL_VERSION
    required_permissions: tuple[str, ...] = ()
    outbound_domains: tuple[str, ...] = ()
    configuration_schema: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.extension_id):
            raise ValueError("extension_id is invalid")
        if not self.version.strip() or not self.name.strip():
            raise ValueError("extension version and name are required")
        if any(not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in (self.build_digest, self.lockfile_digest)):
            raise ValueError("Extension build and lockfile digests must be sha256")
        if not self.extension_types or not set(self.extension_types) <= EXTENSION_TYPES:
            raise ValueError("extension type is invalid")
        if self.sdk_version != SDK_VERSION or self.host_protocol_version != HOST_PROTOCOL_VERSION:
            raise ValueError("unsupported Extension ABI version")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("extension_types", "required_permissions", "outbound_domains"):
            value[key] = list(value[key])
        return value


@dataclass(slots=True)
class InvocationContext:
    user_id: str
    agent_id: str | None
    session_id: str | None
    run_id: str
    root_run_id: str
    task_id: str | None
    action_id: str | None
    idempotency_key: str
    permissions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WriteReceipt:
    action_id: str
    idempotency_key: str
    provider_operation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.action_id or not self.idempotency_key:
            raise ValueError("write receipt requires Runtime-frozen Action identity")


@dataclass(slots=True)
class CapabilityResult:
    status: Literal["succeeded", "accepted", "failed"]
    output: Any = None
    error: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    operation: dict[str, Any] | None = None
    write_receipt: WriteReceipt | None = None

    def __post_init__(self) -> None:
        if self.status == "accepted" and not self.operation:
            raise ValueError("accepted result requires an operation")
        if self.status == "failed" and not self.error:
            raise ValueError("failed result requires an error")


@runtime_checkable
class CapabilityHandler(Protocol):
    async def execute(self, context: InvocationContext, input: dict[str, Any]) -> CapabilityResult: ...


__all__ = [
    "CapabilityDefinition",
    "CapabilityHandler",
    "CapabilityKind",
    "CapabilityRef",
    "CapabilityResult",
    "EXTENSION_TYPES",
    "ExtensionManifest",
    "HOST_PROTOCOL_VERSION",
    "InvocationContext",
    "SDK_VERSION",
    "WriteReceipt",
]
