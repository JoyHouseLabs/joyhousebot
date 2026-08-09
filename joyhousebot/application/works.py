"""Turn durable Run artifacts into versioned, governable, shareable works."""

from __future__ import annotations

import asyncio
import json
import secrets
from hashlib import sha256
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from joyhousebot.application.context import RequestContext
from joyhousebot.application.errors import NotFoundError, ValidationError
from joyhousebot.runtime.action_identity import payload_hash

_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}
_MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024


def _audit_id() -> str:
    return f"waudit_{uuid4().hex}"


def _safe_public_uri(value: Any) -> str | None:
    uri = str(value or "").strip()
    if not uri:
        return None
    parsed = urlparse(uri)
    return uri if parsed.scheme == "https" and parsed.netloc else None


def _bounded(value: Any, *, label: str, maximum: int = _MAX_SNAPSHOT_BYTES) -> None:
    try:
        size = len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode(
                "utf-8"
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be JSON serializable") from exc
    if size > maximum:
        raise ValidationError(f"{label} exceeds {maximum} bytes")


class WorkService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def _source_artifact(
        self, context: RequestContext, run_id: str, artifact_id: str
    ) -> dict[str, Any]:
        artifacts = await asyncio.to_thread(
            self.store.list_runtime_artifacts, run_id, user_id=context.user_id
        )
        artifact = next(
            (item for item in artifacts if item["artifact_id"] == artifact_id), None
        )
        if artifact is None:
            raise NotFoundError("source artifact not found")
        _bounded(artifact.get("content"), label="artifact content")
        if artifact.get("content") is None and artifact.get("uri") and (
            not artifact.get("content_sha256") or not artifact.get("object_version")
        ):
            raise ValidationError(
                "URI artifacts require content_sha256 and object_version before becoming Works"
            )
        return artifact

    @staticmethod
    def _validate_text(value: Any, *, label: str, maximum: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValidationError(f"{label} is required")
        if len(text) > maximum:
            raise ValidationError(f"{label} exceeds {maximum} characters")
        return text

    @staticmethod
    def _public(value: dict[str, Any], *, permission: str = "view") -> dict[str, Any]:
        version = dict(value.get("version") or {})
        return {
            "work_id": value["work_id"],
            "public_slug": value["public_slug"],
            "title": value["title"],
            "description": value["description"],
            "version": version.get("version"),
            "media_type": version.get("media_type"),
            "content": version.get("content"),
            "uri": _safe_public_uri(version.get("uri")),
            "content_sha256": version.get("content_sha256"),
            "change_note": version.get("change_note"),
            "published_at": value.get("published_at"),
            "permission": permission,
        }

    @staticmethod
    def _validate_publishable(value: dict[str, Any]) -> None:
        version = dict(value.get("version") or {})
        if version.get("content") is None and not _safe_public_uri(version.get("uri")):
            raise ValidationError(
                "published works require embedded content or an HTTPS artifact URI"
            )
        if not version.get("content_sha256"):
            raise ValidationError("published works require an immutable content digest")
        if version.get("content") is None and not version.get("source_object_version"):
            raise ValidationError("published URI works require a frozen object version")

    async def create(
        self, context: RequestContext, value: dict[str, Any]
    ) -> dict[str, Any]:
        run_id = str(value.get("run_id") or "")
        artifact_id = str(value.get("artifact_id") or "")
        await self._source_artifact(context, run_id, artifact_id)
        title = self._validate_text(value.get("title"), label="work title", maximum=256)
        description = str(value.get("description") or "").strip()
        if len(description) > 10_000:
            raise ValidationError("work description exceeds 10000 characters")
        classification = str(value.get("data_classification") or "internal")
        if classification not in _CLASSIFICATIONS:
            raise ValidationError("invalid work data classification")
        metadata = dict(value.get("metadata") or {})
        _bounded(metadata, label="work metadata", maximum=64 * 1024)
        identity = context.idempotency_key or uuid4().hex
        work_id = f"work_{payload_hash({'user': context.user_id, 'key': identity})[:32]}"
        return await asyncio.to_thread(
            self.store.create_work_from_artifact,
            value={
                "work_id": work_id,
                "owner_user_id": context.user_id,
                "public_slug": f"w_{secrets.token_urlsafe(18)}",
                "title": title,
                "description": description,
                "data_classification": classification,
                "metadata": metadata,
                "source_run_id": run_id,
                "source_artifact_id": artifact_id,
                "change_note": str(value.get("change_note") or "Initial version")[:2000],
                "created_by": context.principal.subject,
                "audit_id": _audit_id(),
            },
        )

    async def add_version(
        self, context: RequestContext, work_id: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        run_id = str(value.get("run_id") or "")
        artifact_id = str(value.get("artifact_id") or "")
        await self._source_artifact(context, run_id, artifact_id)
        try:
            return await asyncio.to_thread(
                self.store.add_work_version,
                work_id,
                value={
                    "actor_user_id": context.user_id,
                    "source_run_id": run_id,
                    "source_artifact_id": artifact_id,
                    "change_note": str(value.get("change_note") or "")[:2000],
                    "created_by": context.principal.subject,
                    "audit_id": _audit_id(),
                },
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def get(self, context: RequestContext, work_id: str) -> dict[str, Any]:
        value = await asyncio.to_thread(
            self.store.get_work, work_id, expected_user_id=context.user_id
        )
        if value is None:
            raise NotFoundError("work not found")
        return value

    async def list(self, context: RequestContext) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.store.list_works, expected_user_id=context.user_id
        )

    async def update(
        self, context: RequestContext, work_id: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        current = await self.get(context, work_id)
        if current["owner_user_id"] != context.user_id:
            raise NotFoundError("owner work not found")
        if value.get("status") == "published":
            self._validate_publishable(current)
        if "title" in value:
            value["title"] = self._validate_text(
                value["title"], label="work title", maximum=256
            )
        if "description" in value:
            value["description"] = str(value["description"] or "").strip()
            if len(value["description"]) > 10_000:
                raise ValidationError("work description exceeds 10000 characters")
        if "metadata" in value:
            value["metadata"] = dict(value["metadata"] or {})
            _bounded(value["metadata"], label="work metadata", maximum=64 * 1024)
        for field in ("status", "visibility", "data_classification"):
            if field in value and value[field] is None:
                raise ValidationError(f"work {field} cannot be null")
        try:
            return await asyncio.to_thread(
                self.store.update_work,
                work_id,
                value={
                    **value,
                    "owner_user_id": context.user_id,
                    "actor_id": context.principal.subject,
                    "audit_id": _audit_id(),
                },
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def create_share(
        self, context: RequestContext, work_id: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        try:
            share = await asyncio.to_thread(
                self.store.create_work_share,
                work_id,
                value={
                    **value,
                    "share_id": f"share_{uuid4().hex}",
                    "owner_user_id": context.user_id,
                    "token_hash": sha256(token.encode("utf-8")).hexdigest(),
                    "created_by": context.principal.subject,
                    "audit_id": _audit_id(),
                },
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return {**share, "token": token, "path": f"/v1/public/shares/{token}"}

    async def list_shares(
        self, context: RequestContext, work_id: str
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.store.list_work_shares, work_id, expected_user_id=context.user_id
        )

    async def revoke_share(
        self, context: RequestContext, work_id: str, share_id: str
    ) -> dict[str, Any]:
        share = await asyncio.to_thread(
            self.store.revoke_work_share,
            share_id,
            expected_user_id=context.user_id,
            actor_id=context.principal.subject,
            audit_id=_audit_id(),
        )
        if share is None or share["work_id"] != work_id:
            raise NotFoundError("active work share not found")
        return share

    async def grant_collaborator(
        self, context: RequestContext, work_id: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                self.store.grant_work_collaborator,
                work_id,
                value={
                    **value,
                    "owner_user_id": context.user_id,
                    "granted_by": context.principal.subject,
                    "audit_id": _audit_id(),
                },
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def list_collaborators(
        self, context: RequestContext, work_id: str
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.store.list_work_collaborators,
            work_id,
            expected_user_id=context.user_id,
        )

    async def revoke_collaborator(
        self, context: RequestContext, work_id: str, user_id: str
    ) -> None:
        removed = await asyncio.to_thread(
            self.store.revoke_work_collaborator,
            work_id,
            user_id,
            expected_user_id=context.user_id,
            actor_id=context.principal.subject,
            audit_id=_audit_id(),
        )
        if not removed:
            raise NotFoundError("work collaborator not found")

    async def audit(
        self, context: RequestContext, work_id: str
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.store.list_work_audit, work_id, expected_user_id=context.user_id
        )

    async def resolve_public(self, *, slug: str) -> dict[str, Any]:
        value = await asyncio.to_thread(
            self.store.resolve_public_work,
            public_slug=slug,
            audit_id=_audit_id(),
        )
        if value is None:
            raise NotFoundError("published work not found")
        return self._public(value)

    async def resolve_share(self, *, token: str) -> dict[str, Any]:
        if len(token) < 32 or len(token) > 256:
            raise NotFoundError("active work share not found")
        token_hash = sha256(token.encode("utf-8")).hexdigest()
        value = await asyncio.to_thread(
            self.store.resolve_public_work,
            token_hash=token_hash,
            audit_id=_audit_id(),
        )
        if value is None:
            raise NotFoundError("active work share not found")
        return self._public(value, permission=str(value.get("share_permission") or "view"))
