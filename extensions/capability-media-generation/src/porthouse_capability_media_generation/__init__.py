"""Official media-generation Capability extension."""

from .plugin import MediaGenerationPlugin, create_plugin
from .providers import MediaProviderAdapter, MediaProviderRegistry

__all__ = [
    "MediaGenerationPlugin",
    "MediaProviderAdapter",
    "MediaProviderRegistry",
    "create_plugin",
]
