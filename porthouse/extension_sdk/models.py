"""Stable model-provider extension API."""

from porthouse.contracts.extensions import (
    ModelProviderBuildRequest,
    ModelProviderExtension,
    ModelProviderSpec,
)
from porthouse.providers.base import EmbeddingResponse, LLMProvider, LLMResponse, ToolCallRequest
from porthouse.providers.observability import (
    bind_model_observation,
    model_cache_hit,
    model_first_token,
    model_request_failed,
    model_request_finished,
    model_request_started,
)
from porthouse.providers.provider_support import (
    ProviderHTTPError,
    classify_error,
    error_metadata,
    extract_status_code,
    restore_tool_name,
    safe_tool_name,
    sanitize_messages,
    sanitize_tools,
    user_friendly_error,
)
from porthouse.providers.usage import (
    cache_hit_usage,
    missing_usage,
    normalized_usage,
    partial_usage,
)

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "EmbeddingResponse",
    "ModelProviderBuildRequest",
    "ModelProviderExtension",
    "ModelProviderSpec",
    "ProviderHTTPError",
    "ToolCallRequest",
    "bind_model_observation",
    "classify_error",
    "error_metadata",
    "extract_status_code",
    "model_cache_hit",
    "model_first_token",
    "model_request_failed",
    "model_request_finished",
    "model_request_started",
    "restore_tool_name",
    "safe_tool_name",
    "sanitize_messages",
    "sanitize_tools",
    "user_friendly_error",
    "cache_hit_usage",
    "missing_usage",
    "normalized_usage",
    "partial_usage",
]
