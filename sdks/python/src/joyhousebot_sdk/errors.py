"""Stable exceptions mapped from the public v2 error envelope."""

from __future__ import annotations

from typing import Any


class JoyHouseBotError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        field_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.field_path = field_path

    @classmethod
    def from_response(cls, status_code: int, value: Any) -> "JoyHouseBotError":
        error = value.get("error") if isinstance(value, dict) else None
        if not isinstance(error, dict):
            return cls("HTTP_ERROR", f"joyhousebot HTTP {status_code}", status_code=status_code)
        return cls(
            str(error.get("code") or "HTTP_ERROR"),
            str(error.get("message") or f"joyhousebot HTTP {status_code}"),
            status_code=status_code,
            retryable=bool(error.get("retryable", False)),
            field_path=str(error["field_path"]) if error.get("field_path") else None,
        )


class AuthenticationError(JoyHouseBotError):
    pass


class PermissionDeniedError(JoyHouseBotError):
    pass


class NotFoundError(JoyHouseBotError):
    pass


class ConflictError(JoyHouseBotError):
    pass


class ValidationError(JoyHouseBotError):
    pass


class RateLimitError(JoyHouseBotError):
    pass


_BY_STATUS = {
    400: ValidationError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: RateLimitError,
}


def error_from_response(status_code: int, value: Any) -> JoyHouseBotError:
    return _BY_STATUS.get(status_code, JoyHouseBotError).from_response(status_code, value)


__all__ = [
    "AuthenticationError",
    "ConflictError",
    "NotFoundError",
    "PermissionDeniedError",
    "JoyHouseBotError",
    "RateLimitError",
    "ValidationError",
    "error_from_response",
]
