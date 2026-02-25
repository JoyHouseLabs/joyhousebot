"""Common RPC error-boundary helpers for server dispatch."""

from __future__ import annotations

from typing import Any, Callable

from joyhousebot.utils.exceptions import (
    JoyhouseBotError,
    sanitize_error_message,
    classify_exception,
    ErrorCategory,
)


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


def unknown_method_result(
    *,
    method: str,
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
) -> RpcResult:
    """Build standardized unknown-method response."""
    return False, None, rpc_error("INVALID_REQUEST", f"unknown method: {method}", None)


def http_exception_result(
    *,
    method: str,
    exc: Any,
    log_info: Callable[[str, Any, Any], None],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
) -> RpcResult:
    """Map HTTPException-like objects to RPC error payloads."""
    status_code = getattr(exc, "status_code", None)
    detail = getattr(exc, "detail", "")
    log_info("RPC HTTP error method={} status={}", method, status_code)
    return False, None, rpc_error("HTTP_ERROR", str(detail), {"status_code": status_code})


def joyhousebot_error_result(
    *,
    method: str,
    exc: JoyhouseBotError,
    log_warning: Callable[[str, Any], None],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
) -> RpcResult:
    """Map JoyhouseBotError to RPC error payloads with proper categorization."""
    log_warning("RPC method {} failed with {}: {}", method, exc.code, exc.message)
    return False, None, rpc_error(exc.code, exc.message, exc.details)


def unhandled_exception_result(
    *,
    method: str,
    exc: Exception,
    log_exception: Callable[[str, Any], None],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
) -> RpcResult:
    """Map unexpected exceptions to standardized INTERNAL_ERROR responses."""
    code, category, _ = classify_exception(exc)
    sanitized = sanitize_error_message(str(exc))
    log_exception("RPC method {} failed with [{}]: {}", method, code, sanitized)
    details = {"error_code": code, "category": category.value}
    return False, None, rpc_error("INTERNAL_ERROR", sanitized, details)


def classify_http_status(exc: Exception) -> int:
    """Map exception to appropriate HTTP status code."""
    if isinstance(exc, JoyhouseBotError):
        category_to_status = {
            ErrorCategory.VALIDATION: 400,
            ErrorCategory.NOT_FOUND: 404,
            ErrorCategory.PERMISSION: 403,
            ErrorCategory.TIMEOUT: 504,
            ErrorCategory.RATE_LIMIT: 429,
            ErrorCategory.RECOVERABLE: 500,
            ErrorCategory.RETRYABLE: 503,
            ErrorCategory.FATAL: 500,
        }
        return category_to_status.get(exc.category, 500)

    code, category, _ = classify_exception(exc)
    category_to_status = {
        ErrorCategory.VALIDATION: 400,
        ErrorCategory.NOT_FOUND: 404,
        ErrorCategory.PERMISSION: 403,
        ErrorCategory.TIMEOUT: 504,
        ErrorCategory.RATE_LIMIT: 429,
        ErrorCategory.RETRYABLE: 503,
    }
    return category_to_status.get(category, 500)
