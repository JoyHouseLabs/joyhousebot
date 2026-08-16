"""Core model-provider contracts; concrete protocols are extensions."""

from porthouse.providers.base import LLMProvider, LLMResponse

__all__ = [
    "LLMProvider",
    "LLMResponse",
]
