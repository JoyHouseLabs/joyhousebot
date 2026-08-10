"""Utility functions for joyhousebot."""

from joyhousebot.utils.exceptions import (
    ErrorCategory,
    JoyhouseBotError,
    LLMError,
    RateLimitError,
    TimeoutError,
    ToolError,
    ValidationError,
    classify_exception,
    sanitize_error_message,
    tool_error_handler,
)

__all__ = [
    "JoyhouseBotError",
    "ValidationError",
    "TimeoutError",
    "RateLimitError",
    "LLMError",
    "ToolError",
    "ErrorCategory",
    "classify_exception",
    "sanitize_error_message",
    "tool_error_handler",
]
