"""Public, versioned SDK surface for out-of-tree Porthouse extensions."""

from porthouse.contracts.artifacts import Artifact
from porthouse.contracts.capabilities import (
    CapabilityContext,
    CapabilityResult,
    OperationProgressEvent,
    OperationReconciliationResult,
    WriteReceipt,
)
from porthouse.contracts.extensions import (
    EXTENSION_SDK_VERSION,
    RUNTIME_API_VERSION,
    ExtensionManifest,
    ToolConnectorConnectRequest,
    ToolConnectorExtension,
)
from porthouse.contracts.plugins import (
    Plugin,
    PluginComponent,
    PluginManifest,
    PluginQuickstart,
    PluginRegistry,
)
from porthouse.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
    InvocationStatus,
)

__all__ = [
    "Artifact",
    "CapabilityContext",
    "CapabilityDefinition",
    "CapabilityKind",
    "CapabilityRef",
    "CapabilityResult",
    "InvocationStatus",
    "OperationProgressEvent",
    "OperationReconciliationResult",
    "EXTENSION_SDK_VERSION",
    "ExtensionManifest",
    "Plugin",
    "PluginComponent",
    "PluginManifest",
    "PluginQuickstart",
    "PluginRegistry",
    "RUNTIME_API_VERSION",
    "ToolConnectorConnectRequest",
    "ToolConnectorExtension",
    "WriteReceipt",
]
