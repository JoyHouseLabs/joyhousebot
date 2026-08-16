"""Optional local, scope-preserving candidate reranking capability."""

from .plugin import RerankCapabilityPlugin, create_plugin

__all__ = ["RerankCapabilityPlugin", "create_plugin"]
