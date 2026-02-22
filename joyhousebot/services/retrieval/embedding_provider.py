"""Embedding provider using LiteLLM (OpenAI-compatible and others)."""

from __future__ import annotations

from typing import Any

from loguru import logger


def _get_embedding_api_key(config: Any, provider: str) -> str | None:
    """Resolve API key for embedding provider from config.providers."""
    if not config or not getattr(config, "providers", None):
        return None
    name = (provider or "openai").lower()
    p = getattr(config.providers, name, None)
    if p is None and name == "openai":
        p = getattr(config.providers, "openai", None)
    if p is not None and getattr(p, "api_key", None):
        return (p.api_key or "").strip() or None
    return None


class LiteLLMEmbeddingProvider:
    """Embed texts via LiteLLM (OpenAI, Azure, etc.)."""

    def __init__(self, model: str, provider: str = "openai", api_key: str | None = None):
        self.model = model
        self.provider = (provider or "openai").strip().lower()
        self._api_key = (api_key or "").strip() or None

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Sync embed; returns list of vectors. Empty input returns []."""
        if not texts:
            return []
        try:
            import litellm
            # LiteLLM model format: openai/text-embedding-3-small or just model name
            model = self.model
            if self.provider and "/" not in model:
                model = f"{self.provider}/{model}"
            kwargs: dict[str, Any] = {"model": model, "input": texts}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            response = litellm.embedding(**kwargs)
            if response and getattr(response, "data", None):
                out = [d.embedding for d in response.data if getattr(d, "embedding", None)]
                return out[: len(texts)]
            return []
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
            return []

    async def aembed(self, texts: list[str]) -> list[list[float]]:
        """Async embed; returns list of vectors."""
        if not texts:
            return []
        try:
            import litellm
            model = self.model
            if self.provider and "/" not in model:
                model = f"{self.provider}/{model}"
            kwargs: dict[str, Any] = {"model": model, "input": texts, "aembedding": True}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            response = await litellm.aembedding(**kwargs)
            if response and getattr(response, "data", None):
                out = [d.embedding for d in response.data if getattr(d, "embedding", None)]
                return out[: len(texts)]
            return []
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
            return []
