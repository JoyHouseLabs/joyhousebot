"""Stable Extension contracts shared by the Runtime and capability providers.

The contracts intentionally contain no FastAPI, PostgreSQL, or runtime
implementation details.  They are the seam that will later become
``joyhousebot-sdk``.
"""

from joyhousebot.contracts.artifacts import Artifact
from joyhousebot.contracts.capabilities import (
    CapabilityContext,
    CapabilityHandler,
    CapabilityResult,
    OperationProgressEvent,
    OperationReconciler,
    OperationReconciliationResult,
    WriteReceipt,
)
from joyhousebot.contracts.capability_extensions import (
    CapabilityExtension,
    CapabilityExtensionManifest,
    CapabilityExtensionRegistrar,
)
from joyhousebot.contracts.extensions import (
    EXTENSION_SDK_VERSION,
    RUNTIME_API_VERSION,
    CapabilityConnectorConnectRequest,
    CapabilityConnectorExtension,
    ExtensionManifest,
)
from joyhousebot.contracts.tools import Tool

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
    "CapabilityExtension",
    "CapabilityExtensionManifest",
    "CapabilityExtensionRegistrar",
    "RUNTIME_API_VERSION",
    "CapabilityConnectorConnectRequest",
    "CapabilityConnectorExtension",
    "Tool",
]
