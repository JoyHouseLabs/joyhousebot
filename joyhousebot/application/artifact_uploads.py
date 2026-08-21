"""Issue scoped Host Artifact grants and stream their one-use uploads."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
from collections.abc import AsyncIterable
from typing import Any
from uuid import uuid4

from joyhousebot.application.context import RequestContext
from joyhousebot.application.errors import ConflictError, NotFoundError, ValidationError

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_PROVENANCE_FIELDS = frozenset(
    {
        "host_id",
        "host_version",
        "host_build_digest",
        "host_extension_id",
        "host_extension_version",
        "host_extension_build_digest",
        "host_extension_lockfile_digest",
        "host_sdk_version",
        "trace_id",
    }
)


class ArtifactUploadService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def create(
        self,
        context: RequestContext,
        *,
        run_id: str,
        reconciliation_id: str,
        operation_id: str,
        name: str,
        media_type: str,
        content_sha256: str,
        byte_size: int,
        expires_in_seconds: int,
        provenance: dict[str, Any] | None = None,
    ) -> tuple[Any, str]:
        record = await asyncio.to_thread(
            self.store.get_operation_reconciliation,
            reconciliation_id,
            expected_user_id=context.user_id,
        )
        if record is None or record.run_id != run_id:
            raise NotFoundError("operation reconciliation not found")
        action = await asyncio.to_thread(self.store.get_action_intent, record.action_id)
        if action is None:
            raise ConflictError("operation Action is unavailable")
        safe_name, safe_media, digest = self._validate_metadata(
            name=name,
            media_type=media_type,
            content_sha256=content_sha256,
            byte_size=byte_size,
        )
        token = secrets.token_urlsafe(32)
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        safe_provenance = dict(provenance or {})
        if set(safe_provenance) - _PROVENANCE_FIELDS:
            raise ValidationError("Artifact provenance contains unsupported fields")
        if len(
            json.dumps(
                safe_provenance,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ) > 16_384:
            raise ValidationError("Artifact provenance exceeds 16384 bytes")
        try:
            grant = await asyncio.to_thread(
                self.store.create_artifact_upload_grant,
                grant_id=f"grant_{uuid4().hex}",
                token_fingerprint=fingerprint,
                user_id=context.user_id,
                run_id=run_id,
                task_id=action.task_id,
                action_id=record.action_id,
                reconciliation_id=reconciliation_id,
                operation_id=operation_id,
                artifact_id=f"artifact_{uuid4().hex}",
                name=safe_name,
                media_type=safe_media,
                expected_sha256=digest,
                expected_size=byte_size,
                expires_in_seconds=expires_in_seconds,
                provenance=safe_provenance,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return grant, token

    async def upload(
        self,
        grant_id: str,
        token: str,
        chunks: AsyncIterable[bytes],
        *,
        operation_id: str,
        action_id: str,
        media_type: str,
        content_sha256: str,
        content_length: int,
    ) -> Any:
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        grant = await asyncio.to_thread(
            self.store.get_artifact_upload_grant_by_token,
            grant_id,
            token_fingerprint=fingerprint,
        )
        if grant is None:
            raise NotFoundError("Artifact upload grant not found")
        if grant.status != "issued":
            raise ConflictError("Artifact upload grant is no longer usable")
        normalized_media = media_type.split(";", 1)[0].strip().lower()
        if (
            operation_id != grant.operation_id
            or action_id != grant.action_id
            or normalized_media != grant.media_type
            or content_sha256.strip().lower() != grant.expected_sha256
            or content_length != grant.expected_size
        ):
            raise ValidationError("Artifact upload does not match its frozen grant")
        object_store = getattr(self.store, "artifact_upload_store", None)
        if object_store is None:
            raise ConflictError("Runtime Artifact upload storage is not configured")
        maximum = int(getattr(self.store, "artifact_upload_max_bytes", 250 * 1024 * 1024))
        try:
            stored = await object_store.put_stream(
                chunks,
                expected_sha256=grant.expected_sha256,
                expected_size=grant.expected_size,
                max_bytes=maximum,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        committed = await asyncio.to_thread(
            self.store.commit_artifact_upload,
            grant_id,
            token_fingerprint=fingerprint,
            operation_id=operation_id,
            action_id=action_id,
            media_type=normalized_media,
            content_sha256=stored.content_sha256,
            byte_size=stored.byte_size,
            storage_uri=stored.uri,
            object_version=stored.object_version,
        )
        if committed is None:
            raise ConflictError("Artifact upload grant was consumed or expired")
        return committed

    def _validate_metadata(
        self, *, name: str, media_type: str, content_sha256: str, byte_size: int
    ) -> tuple[str, str, str]:
        safe_name = name.strip()
        if not safe_name or len(safe_name) > 500 or any(
            char in safe_name for char in ("\x00", "\r", "\n")
        ):
            raise ValidationError("Artifact name must contain 1-500 safe characters")
        safe_media = media_type.split(";", 1)[0].strip().lower()
        if not _MEDIA_TYPE.fullmatch(safe_media):
            raise ValidationError("Artifact media_type is invalid")
        digest = content_sha256.strip().lower()
        if not _SHA256.fullmatch(digest):
            raise ValidationError("Artifact content_sha256 must be lowercase SHA-256")
        maximum = int(getattr(self.store, "artifact_upload_max_bytes", 250 * 1024 * 1024))
        if byte_size < 0 or byte_size > maximum:
            raise ValidationError(f"Artifact exceeds the {maximum} byte upload limit")
        return safe_name, safe_media, digest
