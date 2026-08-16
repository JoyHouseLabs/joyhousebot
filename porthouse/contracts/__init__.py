"""Stable extension contracts shared by the framework and business plugins.

The contracts intentionally contain no FastAPI, PostgreSQL, or runtime
implementation details.  They are the seam that will later become
``porthouse-sdk``.
"""

from porthouse.contracts.artifacts import Artifact
from porthouse.contracts.capabilities import (
    CapabilityContext,
    CapabilityHandler,
    CapabilityResult,
    OperationProgressEvent,
    OperationReconciler,
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
    PLUGIN_COMPONENT_TYPES,
    Plugin,
    PluginComponent,
    PluginManifest,
    PluginQuickstart,
    PluginRegistry,
)
from porthouse.contracts.tools import Tool

__all__ = [
    "Artifact",
    "CapabilityContext",
    "CapabilityHandler",
    "CapabilityResult",
    "OperationReconciler",
    "OperationProgressEvent",
    "OperationReconciliationResult",
    "WriteReceipt",
    "EXTENSION_SDK_VERSION",
    "ExtensionManifest",
    "Plugin",
    "PLUGIN_COMPONENT_TYPES",
    "PluginComponent",
    "PluginManifest",
    "PluginQuickstart",
    "PluginRegistry",
    "RUNTIME_API_VERSION",
    "ToolConnectorConnectRequest",
    "ToolConnectorExtension",
    "Tool",
]
