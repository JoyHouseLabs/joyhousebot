"""Shared helpers for consistent HTTP error detail formatting."""

from __future__ import annotations


def unknown_error_detail(exc: Exception | None) -> str:
    """Format generic unknown-error detail consistently across endpoints."""
    return str(exc) if exc else "Unknown error"

