"""Stable extension contracts shared by the framework and business plugins.

The contracts intentionally contain no FastAPI, PostgreSQL, or runtime
implementation details.  They are the seam that will later become
``joyhousebot-sdk``.
"""

from joyhousebot.contracts.artifacts import Artifact
from joyhousebot.contracts.capabilities import (
    CapabilityContext,
    CapabilityHandler,
    CapabilityResult,
    OperationReconciler,
    OperationReconciliationResult,
    WriteReceipt,
)
from joyhousebot.contracts.extensions import (
    EXTENSION_SDK_VERSION,
    RUNTIME_API_VERSION,
    ExtensionManifest,
    ToolConnectorConnectRequest,
    ToolConnectorExtension,
)
from joyhousebot.contracts.plugins import (
    PLUGIN_COMPONENT_TYPES,
    Plugin,
    PluginComponent,
    PluginManifest,
    PluginQuickstart,
    PluginRegistry,
)
from joyhousebot.contracts.tools import Tool

__all__ = [
    "Artifact",
    "CapabilityContext",
    "CapabilityHandler",
    "CapabilityResult",
    "OperationReconciler",
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
