"""Core model-provider contracts; concrete protocols are extensions."""

from joyhousebot.providers.base import LLMProvider, LLMResponse

__all__ = [
    "LLMProvider",
    "LLMResponse",
]
