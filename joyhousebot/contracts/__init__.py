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
)
from joyhousebot.contracts.events import DomainEvent
from joyhousebot.contracts.plugins import (
    Plugin,
    PluginComponent,
    PluginHealthCheck,
    PluginHealthContext,
    PluginHealthResult,
    PluginManifest,
    PluginQuickstart,
    PluginRegistry,
)
from joyhousebot.contracts.projections import (
    ProjectionContext,
    ProjectionProvider,
    RunProjectionQueries,
    ScopedRunProjectionQueries,
)

__all__ = [
    "Artifact",
    "CapabilityContext",
    "CapabilityHandler",
    "CapabilityResult",
    "DomainEvent",
    "Plugin",
    "PluginComponent",
    "PluginHealthCheck",
    "PluginHealthContext",
    "PluginHealthResult",
    "PluginManifest",
    "PluginQuickstart",
    "PluginRegistry",
    "ProjectionContext",
    "ProjectionProvider",
    "RunProjectionQueries",
    "ScopedRunProjectionQueries",
]
