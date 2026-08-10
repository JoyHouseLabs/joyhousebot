"""JoyhouseBot OpenAI-compatible model provider extension."""

from joyhousebot_provider_openai_compatible.provider import (
    OPENAI_COMPATIBLE_PROVIDER_EXTENSION,
    OpenAICompatibleProvider,
    create_extension,
)

__all__ = [
    "OPENAI_COMPATIBLE_PROVIDER_EXTENSION",
    "OpenAICompatibleProvider",
    "create_extension",
]
