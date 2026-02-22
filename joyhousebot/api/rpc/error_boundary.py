"""Common RPC error-boundary helpers for server dispatch."""

from __future__ import annotations

from typing import Any, Callable


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


def unhandled_exception_result(
    *,
    method: str,
    exc: Exception,
    log_exception: Callable[[str, Any], None],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
) -> RpcResult:
    """Map unexpected exceptions to standardized INTERNAL_ERROR responses."""
    log_exception("RPC method {} failed", method)
    return False, None, rpc_error("INTERNAL_ERROR", str(exc), None)

