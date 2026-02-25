"""
Exception hierarchy and error handling utilities for joyhousebot.

Provides:
- Custom exception classes with error codes
- Error categorization (recoverable, retryable, fatal)
- Safe error message formatting (no sensitive data leak)
- Decorators for consistent exception handling
"""

from __future__ import annotations

import asyncio
import functools
import json
import re
from enum import Enum
from typing import Any, Callable, TypeVar

from loguru import logger

F = TypeVar("F", bound=Callable[..., Any])


class ErrorCategory(Enum):
    """Error categories for classification."""
    RECOVERABLE = "recoverable"
    RETRYABLE = "retryable"
    FATAL = "fatal"
    VALIDATION = "validation"
    NOT_FOUND = "not_found"
    PERMISSION = "permission"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"


class JoyhouseBotError(Exception):
    """Base exception for all joyhousebot errors."""

    def __init__(
        self,
        message: str,
        code: str = "UNKNOWN_ERROR",
        category: ErrorCategory = ErrorCategory.FATAL,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.category = category
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "category": self.category.value,
            "details": self.details,
        }

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class ValidationError(JoyhouseBotError):
    """Input validation error."""

    def __init__(self, message: str, field: str | None = None):
        details = {"field": field} if field else {}
        super().__init__(message, code="VALIDATION_ERROR", category=ErrorCategory.VALIDATION, details=details)


class NotFoundError(JoyhouseBotError):
    """Resource not found error."""

    def __init__(self, resource_type: str, resource_id: str):
        super().__init__(
            f"{resource_type} not found: {resource_id}",
            code="NOT_FOUND",
            category=ErrorCategory.NOT_FOUND,
            details={"resource_type": resource_type, "resource_id": resource_id},
        )


class PermissionError(JoyhouseBotError):
    """Permission denied error."""

    def __init__(self, message: str, resource: str | None = None):
        details = {"resource": resource} if resource else {}
        super().__init__(message, code="PERMISSION_DENIED", category=ErrorCategory.PERMISSION, details=details)


class TimeoutError(JoyhouseBotError):
    """Operation timeout error."""

    def __init__(self, operation: str, timeout_seconds: float):
        super().__init__(
            f"Operation '{operation}' timed out after {timeout_seconds}s",
            code="TIMEOUT",
            category=ErrorCategory.TIMEOUT,
            details={"operation": operation, "timeout_seconds": timeout_seconds},
        )


class RateLimitError(JoyhouseBotError):
    """Rate limit exceeded error."""

    def __init__(self, service: str, retry_after: float | None = None):
        message = f"Rate limit exceeded for {service}"
        if retry_after:
            message += f", retry after {retry_after}s"
        super().__init__(
            message,
            code="RATE_LIMIT",
            category=ErrorCategory.RATE_LIMIT,
            details={"service": service, "retry_after": retry_after},
        )


class LLMError(JoyhouseBotError):
    """LLM provider error."""

    def __init__(
        self,
        message: str,
        provider: str | None = None,
        model: str | None = None,
        is_retryable: bool = False,
    ):
        category = ErrorCategory.RETRYABLE if is_retryable else ErrorCategory.FATAL
        super().__init__(
            message,
            code="LLM_ERROR",
            category=category,
            details={"provider": provider, "model": model, "is_retryable": is_retryable},
        )


class ToolError(JoyhouseBotError):
    """Tool execution error."""

    def __init__(
        self,
        tool_name: str,
        message: str,
        is_recoverable: bool = True,
    ):
        category = ErrorCategory.RECOVERABLE if is_recoverable else ErrorCategory.FATAL
        super().__init__(
            f"Tool '{tool_name}' error: {message}",
            code="TOOL_ERROR",
            category=category,
            details={"tool_name": tool_name, "is_recoverable": is_recoverable},
        )


class ChannelError(JoyhouseBotError):
    """Channel communication error."""

    def __init__(self, channel: str, message: str, is_retryable: bool = False):
        category = ErrorCategory.RETRYABLE if is_retryable else ErrorCategory.FATAL
        super().__init__(
            f"Channel '{channel}' error: {message}",
            code="CHANNEL_ERROR",
            category=category,
            details={"channel": channel, "is_retryable": is_retryable},
        )


class PluginError(JoyhouseBotError):
    """Plugin execution error."""

    def __init__(self, plugin_id: str, message: str, is_retryable: bool = False):
        category = ErrorCategory.RETRYABLE if is_retryable else ErrorCategory.FATAL
        super().__init__(
            f"Plugin '{plugin_id}' error: {message}",
            code="PLUGIN_ERROR",
            category=category,
            details={"plugin_id": plugin_id, "is_retryable": is_retryable},
        )


_SENSITIVE_PATTERNS = [
    re.compile(r"(api[_-]?key|token|secret|password|auth)[=:]\s*['\"]?([^\s'\"]+)['\"]?", re.IGNORECASE),
    re.compile(r"bearer\s+[a-zA-Z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(r"[a-zA-Z0-9]{32,}"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"xox[baprs]-[a-zA-Z0-9\-]+"),
    re.compile(r"[0-9]{10,}:[a-zA-Z0-9_-]{30,}"),
]


def sanitize_error_message(message: str, replacement: str = "[REDACTED]") -> str:
    """Remove sensitive information from error messages."""
    sanitized = message
    for pattern in _SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def classify_exception(exc: Exception) -> tuple[str, ErrorCategory, bool]:
    """
    Classify an exception and return (error_code, category, should_retry).

    Returns:
        Tuple of (error_code, category, should_retry)
    """
    exc_str = str(exc).lower()

    if isinstance(exc, JoyhouseBotError):
        return exc.code, exc.category, exc.category in (ErrorCategory.RETRYABLE, ErrorCategory.RATE_LIMIT)

    if isinstance(exc, FileNotFoundError):
        return "FILE_NOT_FOUND", ErrorCategory.NOT_FOUND, False

    if isinstance(exc, PermissionError):
        return "PERMISSION_DENIED", ErrorCategory.PERMISSION, False

    if isinstance(exc, asyncio.TimeoutError):
        return "TIMEOUT", ErrorCategory.TIMEOUT, True

    if isinstance(exc, ConnectionError):
        return "CONNECTION_ERROR", ErrorCategory.RETRYABLE, True

    if isinstance(exc, json.JSONDecodeError):
        return "JSON_PARSE_ERROR", ErrorCategory.VALIDATION, False

    if isinstance(exc, ValueError):
        return "INVALID_VALUE", ErrorCategory.VALIDATION, False

    if isinstance(exc, KeyError):
        return "MISSING_KEY", ErrorCategory.VALIDATION, False

    if isinstance(exc, TypeError):
        return "TYPE_ERROR", ErrorCategory.VALIDATION, False

    if "rate limit" in exc_str or "429" in exc_str:
        return "RATE_LIMIT", ErrorCategory.RATE_LIMIT, True

    if "timeout" in exc_str or "timed out" in exc_str:
        return "TIMEOUT", ErrorCategory.TIMEOUT, True

    if "not found" in exc_str or "404" in exc_str:
        return "NOT_FOUND", ErrorCategory.NOT_FOUND, False

    if "permission" in exc_str or "forbidden" in exc_str or "403" in exc_str:
        return "PERMISSION_DENIED", ErrorCategory.PERMISSION, False

    if "unauthorized" in exc_str or "401" in exc_str:
        return "UNAUTHORIZED", ErrorCategory.PERMISSION, False

    if "connection" in exc_str or "network" in exc_str:
        return "CONNECTION_ERROR", ErrorCategory.RETRYABLE, True

    return "INTERNAL_ERROR", ErrorCategory.FATAL, False


def format_tool_error(tool_name: str, exc: Exception, include_details: bool = False) -> str:
    """Format an exception as a tool error response string."""
    code, category, _ = classify_exception(exc)

    if isinstance(exc, JoyhouseBotError):
        message = exc.message
    else:
        message = sanitize_error_message(str(exc))

    if include_details:
        return f"Error [{code}] ({category.value}): {message}"
    return f"Error: {message}"


def tool_error_handler(
    default_message: str = "Operation failed",
    log_errors: bool = True,
    include_traceback: bool = False,
) -> Callable[[F], F]:
    """
    Decorator for consistent tool error handling.

    Usage:
        @tool_error_handler("Failed to read file")
        async def execute(self, path: str, **kwargs) -> str:
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> str:
            try:
                return await func(*args, **kwargs)
            except JoyhouseBotError as e:
                if log_errors:
                    logger.warning(f"Tool error: {e.code} - {e.message}")
                return f"Error: {e.message}"
            except FileNotFoundError as e:
                if log_errors:
                    logger.debug(f"File not found: {e}")
                return "Error: File not found"
            except PermissionError as e:
                if log_errors:
                    logger.warning(f"Permission denied: {sanitize_error_message(str(e))}")
                return "Error: Permission denied"
            except asyncio.TimeoutError:
                if log_errors:
                    logger.warning("Operation timed out")
                return "Error: Operation timed out"
            except Exception as e:
                code, category, _ = classify_exception(e)
                sanitized = sanitize_error_message(str(e))
                if log_errors:
                    if include_traceback:
                        logger.exception(f"Unexpected error [{code}]: {sanitized}")
                    else:
                        logger.error(f"Unexpected error [{code}]: {sanitized}")
                return f"Error: {default_message}"

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> str:
            try:
                return func(*args, **kwargs)
            except JoyhouseBotError as e:
                if log_errors:
                    logger.warning(f"Tool error: {e.code} - {e.message}")
                return f"Error: {e.message}"
            except FileNotFoundError as e:
                if log_errors:
                    logger.debug(f"File not found: {e}")
                return "Error: File not found"
            except PermissionError as e:
                if log_errors:
                    logger.warning(f"Permission denied: {sanitize_error_message(str(e))}")
                return "Error: Permission denied"
            except Exception as e:
                code, category, _ = classify_exception(e)
                sanitized = sanitize_error_message(str(e))
                if log_errors:
                    if include_traceback:
                        logger.exception(f"Unexpected error [{code}]: {sanitized}")
                    else:
                        logger.error(f"Unexpected error [{code}]: {sanitized}")
                return f"Error: {default_message}"

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def safe_execute(
    func: Callable[..., Any],
    *args: Any,
    default: str = "Error: Operation failed",
    **kwargs: Any,
) -> str:
    """Safely execute a function and return a string result."""
    try:
        result = func(*args, **kwargs)
        if isinstance(result, str):
            return result
        return str(result)
    except Exception as e:
        logger.debug(f"safe_execute error: {sanitize_error_message(str(e))}")
        return default
