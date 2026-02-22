"""Shared service-layer error types."""

from __future__ import annotations


class ServiceError(Exception):
    """Domain error raised by shared services."""

    def __init__(self, *, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

