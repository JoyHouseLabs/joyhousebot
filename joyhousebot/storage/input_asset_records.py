"""Durable metadata for immutable files attached to Runtime Runs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InputAssetRecord:
    asset_id: str
    user_id: str
    original_name: str
    media_type: str
    content_sha256: str
    byte_size: int
    storage_uri: str
    object_version: str
    status: str
    idempotency_key: str
    created_at: str
    deleted_at: str | None = None


__all__ = ["InputAssetRecord"]
