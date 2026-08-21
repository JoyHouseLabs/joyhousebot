"""Official media-generation Capability extension."""

from .extension import MediaGenerationExtension, create_extension
from .providers import MediaProviderAdapter, MediaProviderRegistry

__all__ = [
    "MediaGenerationExtension",
    "MediaProviderAdapter",
    "MediaProviderRegistry",
    "create_extension",
]
