"""Authenticated use cases for immutable Runtime input assets."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterable
from typing import Any
from uuid import uuid4

from porthouse.application.context import RequestContext
from porthouse.application.errors import ConflictError, NotFoundError, ValidationError

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class InputAssetService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def upload(
        self,
        context: RequestContext,
        chunks: AsyncIterable[bytes],
        *,
        original_name: str,
        media_type: str,
        content_sha256: str,
        content_length: int,
    ) -> tuple[Any, bool]:
        if not context.idempotency_key:
            raise ValidationError("Input Asset upload requires an Idempotency-Key header")
        name = original_name.strip()
        if not name or len(name) > 500 or any(char in name for char in ("\x00", "\r", "\n")):
            raise ValidationError("file_name must contain 1-500 safe characters")
        digest = content_sha256.strip().lower()
        if not _SHA256.fullmatch(digest):
            raise ValidationError("X-Content-SHA256 must be a lowercase SHA-256 hex digest")
        if content_length < 0:
            raise ValidationError("Content-Length is required")
        object_store = getattr(self.store, "input_asset_store", None)
        if object_store is None:
            raise ConflictError("Runtime Input Asset storage is not configured")
        maximum = int(getattr(self.store, "input_asset_max_bytes", 25 * 1024 * 1024))
        try:
            stored = await object_store.put_stream(
                chunks,
                expected_sha256=digest,
                expected_size=content_length,
                max_bytes=maximum,
            )
            return await asyncio.to_thread(
                self.store.create_input_asset,
                asset_id=f"input_{uuid4().hex}",
                user_id=context.user_id,
                original_name=name,
                media_type=(media_type.split(";", 1)[0].strip().lower() or "application/octet-stream"),
                content_sha256=stored.content_sha256,
                byte_size=stored.byte_size,
                storage_uri=stored.uri,
                object_version=stored.object_version,
                idempotency_key=context.idempotency_key,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def get(self, context: RequestContext, asset_id: str) -> Any:
        record = await asyncio.to_thread(
            self.store.get_input_asset, asset_id, expected_user_id=context.user_id
        )
        if record is None:
            raise NotFoundError("Input Asset not found")
        return record

    async def delete(self, context: RequestContext, asset_id: str) -> Any:
        try:
            record = await asyncio.to_thread(
                self.store.delete_input_asset,
                asset_id,
                expected_user_id=context.user_id,
                actor_id=context.principal.subject,
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        if record is None:
            raise NotFoundError("Input Asset not found")
        return record


__all__ = ["InputAssetService"]
