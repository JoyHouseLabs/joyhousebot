"""Fail-closed provider used by an extension-free execution Worker."""

from __future__ import annotations

from typing import Any

from joyhousebot.providers.base import LLMProvider, LLMResponse


class UnconfiguredModelProvider(LLMProvider):
    """Keep the Core Worker healthy without pretending a model is available."""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        del messages, tools, model, max_tokens, temperature
        raise RuntimeError(
            "no model provider extension is deployment-allowed; configure extensions.allowedIds "
            "and an exact runtime.bootstrapModel"
        )

    def get_default_model(self) -> str:
        return "unconfigured/model"


__all__ = ["UnconfiguredModelProvider"]
