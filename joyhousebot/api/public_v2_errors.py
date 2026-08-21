"""Stable error envelope for the public Owner/Installation execution API."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict


class PublicError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool
    field_path: str | None = None


class PublicErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: PublicError


PUBLIC_ERROR_RESPONSES = {
    400: {"model": PublicErrorEnvelope, "description": "Invalid request"},
    401: {"model": PublicErrorEnvelope, "description": "Authentication failed"},
    403: {"model": PublicErrorEnvelope, "description": "Authority or scope denied"},
    404: {"model": PublicErrorEnvelope, "description": "Resource not found"},
    409: {"model": PublicErrorEnvelope, "description": "State or idempotency conflict"},
    422: {"model": PublicErrorEnvelope, "description": "Schema validation failed"},
    429: {"model": PublicErrorEnvelope, "description": "Rate limited"},
    500: {"model": PublicErrorEnvelope, "description": "Internal failure"},
    503: {"model": PublicErrorEnvelope, "description": "Dependency unavailable"},
}


def public_error_response(
    status: int,
    code: str,
    message: str,
    *,
    retryable: bool | None = None,
    field_path: str | None = None,
) -> JSONResponse:
    resolved_retryable = status in {408, 425, 429} or status >= 500
    content: dict[str, Any] = {
        "code": code,
        "message": str(message)[:8000],
        "retryable": resolved_retryable if retryable is None else retryable,
    }
    if field_path:
        content["field_path"] = field_path
    return JSONResponse(status_code=status, content={"error": content})


def is_public_v2_path(path: str) -> bool:
    return path == "/v2" or path.startswith("/v2/")


__all__ = [
    "PUBLIC_ERROR_RESPONSES",
    "PublicErrorEnvelope",
    "is_public_v2_path",
    "public_error_response",
]
