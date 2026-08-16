"""Stable application errors independent of HTTP or worker transports."""

from __future__ import annotations


class ApplicationError(Exception):
    code = "application_error"


class NotFoundError(ApplicationError):
    code = "not_found"


class ConflictError(ApplicationError):
    code = "conflict"


class AuthorizationError(ApplicationError):
    code = "forbidden"


class ValidationError(ApplicationError):
    code = "invalid_request"
