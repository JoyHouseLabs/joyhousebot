"""
Pluggable vector layer (optional). Not required for MVP.

Enable when: chunk count > vector_threshold_chunks, or config.tools.retrieval.vector_enabled is True.
Backends: none (default), chroma, qdrant, pgvector.
"""

from pathlib import Path
from typing import Any

from joyhousebot.services.retrieval.embedding_provider import LiteLLMEmbeddingProvider
from joyhousebot.services.retrieval.embedding_provider import _get_embedding_api_key


def should_enable_vector(
    workspace: Path,
    config: Any,
    current_chunk_count: int | None = None,
) -> bool:
    """
    Return True if vector retrieval should be enabled.

    Thresholds (any satisfied):
    - config.tools.retrieval.vector_enabled is True
    - current_chunk_count > vector_threshold_chunks (default 50_000)
    """
    if config is None:
        return False
    retrieval = getattr(config.tools, "retrieval", None)
    if retrieval is None:
        return False
    if getattr(retrieval, "vector_enabled", False):
        return True
    threshold = getattr(retrieval, "vector_threshold_chunks", 50_000)
    if current_chunk_count is not None and threshold is not None and current_chunk_count > threshold:
        return True
    return False


def get_embedding_provider(config: Any):  # -> LiteLLMEmbeddingProvider | None
    """Return configured embedding provider or None if not configured."""
    if config is None:
        return None
    retrieval = getattr(config.tools, "retrieval", None)
    if retrieval is None or not getattr(retrieval, "vector_enabled", False):
        return None
    model = (getattr(retrieval, "embedding_model", "") or "").strip()
    if not model:
        return None
    provider = (getattr(retrieval, "embedding_provider", "") or "openai").strip() or "openai"
    api_key = _get_embedding_api_key(config, provider)
    return LiteLLMEmbeddingProvider(model=model, provider=provider, api_key=api_key)


def get_memory_embedding_provider(config: Any):  # -> LiteLLMEmbeddingProvider | None
    """Return embedding provider for memory: re-ranking (memory_vector_enabled) or sqlite_vector backend."""
    if config is None:
        return None
    retrieval = getattr(config.tools, "retrieval", None)
    if retrieval is None:
        return None
    backend = (getattr(retrieval, "memory_backend", "builtin") or "builtin").strip().lower()
    use_vector = getattr(retrieval, "memory_vector_enabled", False) or backend in ("sqlite_vector", "auto")
    if not use_vector:
        return None
    model = (getattr(retrieval, "embedding_model", "") or "").strip()
    if not model:
        return None
    provider = (getattr(retrieval, "embedding_provider", "") or "openai").strip() or "openai"
    api_key = _get_embedding_api_key(config, provider)
    return LiteLLMEmbeddingProvider(model=model, provider=provider, api_key=api_key)


def get_vector_store(workspace: Path, config: Any):  # -> ChromaVectorStore | None
    """Return configured vector store or None if not configured."""
    if config is None:
        return None
    if not should_enable_vector(workspace, config):
        return None
    retrieval = getattr(config.tools, "retrieval", None)
    if retrieval is None:
        return None
    backend = (getattr(retrieval, "vector_backend", "") or "").strip().lower()
    if backend == "chroma":
        try:
            from joyhousebot.services.retrieval.chroma_store import ChromaVectorStore
            return ChromaVectorStore(workspace)
        except RuntimeError:
            return None
    return None
