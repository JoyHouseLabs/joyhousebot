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
    def __init__(self, store: Any, *, app_releases: Any | None = None) -> None:
        self.store = store
        self.app_releases = app_releases

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
        return {**share, "token": token, "path": f"/shares/v1/tokens/{token}"}

    @staticmethod
    def _classification_allowed(current: str, maximum: str) -> bool:
        levels = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
        return levels[current] <= levels[maximum]

    @staticmethod
    def _media_type_allowed(media_type: str, declared: list[str]) -> bool:
        return "*/*" in declared or media_type in declared or any(
            item.endswith("/*") and media_type.startswith(item[:-1]) for item in declared
        )

    async def list_consumers(
        self, context: RequestContext, work_id: str
    ) -> list[dict[str, Any]]:
        if context.principal.app_client_id:
            raise ValidationError("delegated App credentials cannot discover Work consumers")
        work = await self.get(context, work_id)
        if self.app_releases is None:
            return []
        installations = await self.app_releases.list_installed(
            user_id=context.user_id, active_only=True
        )
        media_type = str((work.get("version") or {}).get("media_type") or "")
        consumers: list[dict[str, Any]] = []
        for installation in installations:
            for consumer in list(installation.get("work_consumers") or []):
                supported_media_types = [str(item) for item in consumer.get("media_types") or []]
                maximum = str(consumer.get("max_data_classification") or "internal")
                if not self._media_type_allowed(media_type, supported_media_types):
                    continue
                if not self._classification_allowed(work["data_classification"], maximum):
                    continue
                consumers.append(
                    {
                        "installation_id": installation["installation_id"],
                        "app_id": installation["app_id"],
                        "app_version": installation["version"],
                        "app_name": installation["name"],
                        "consumer_id": str(consumer["consumer_id"]),
                        "name": str(consumer["name"]),
                        "description": str(consumer.get("description") or ""),
                        "purposes": list(consumer.get("purposes") or []),
                        "media_types": supported_media_types,
                        "input_schema": dict(consumer.get("input_schema") or {}),
                    }
                )
        return consumers

    async def create_handoff(
        self, context: RequestContext, work_id: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        if context.principal.app_client_id:
            raise ValidationError("delegated App credentials cannot authorize a Work handoff")
        if not context.idempotency_key:
            raise ValidationError("Work handoff requires an Idempotency-Key")
        work = await self.get(context, work_id)
        if work["owner_user_id"] != context.user_id:
            raise NotFoundError("owner Work not found")
        if work["status"] == "archived":
            raise ValidationError("archived Works cannot be handed off")
        requested_version = int(value.get("work_version") or work["current_version"])
        if requested_version != int(work["current_version"]):
            raise ValidationError("select the current Work version before creating a handoff")
        consumer = next(
            (
                item
                for item in await self.list_consumers(context, work_id)
                if item["installation_id"] == value["installation_id"]
                and item["consumer_id"] == value["consumer_id"]
            ),
            None,
        )
        if consumer is None:
            raise ValidationError("installed App does not declare a compatible Work consumer")
        purpose = str(value["purpose"])
        if purpose not in consumer["purposes"]:
            raise ValidationError("Work handoff purpose is not declared by the selected App")
        try:
            return await asyncio.to_thread(
                self.store.create_work_handoff,
                value={
                    "handoff_id": f"handoff_{uuid4().hex}",
                    "work_id": work_id,
                    "work_version": requested_version,
                    "owner_user_id": context.user_id,
                    "installation_id": consumer["installation_id"],
                    "app_id": consumer["app_id"],
                    "app_version": consumer["app_version"],
                    "consumer_id": consumer["consumer_id"],
                    "purpose": purpose,
                    "idempotency_key": context.idempotency_key,
                    "created_by": context.principal.subject,
                    "audit_id": _audit_id(),
                },
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def list_handoffs(
        self, context: RequestContext, work_id: str
    ) -> list[dict[str, Any]]:
        if context.principal.app_client_id:
            raise ValidationError("delegated App credentials cannot list a Work's handoffs")
        await self.get(context, work_id)
        return await asyncio.to_thread(
            self.store.list_work_handoffs, work_id, expected_user_id=context.user_id
        )

    async def handoff_input(
        self, context: RequestContext, handoff_id: str
    ) -> dict[str, Any]:
        installation_id = context.principal.app_installation_id
        if not context.principal.app_client_id or not installation_id:
            raise ValidationError("Work handoff input requires a delegated App credential")
        value = await asyncio.to_thread(
            self.store.read_work_handoff_input,
            handoff_id,
            expected_user_id=context.user_id,
            installation_id=installation_id,
            actor_id=context.principal.subject,
            audit_id=_audit_id(),
        )
        if value is None:
            raise NotFoundError("active Work handoff input not found")
        return value

    async def add_handoff_receipt(
        self, context: RequestContext, handoff_id: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        if not context.idempotency_key:
            raise ValidationError("Work handoff receipt requires an Idempotency-Key")
        if (
            not context.principal.app_client_id
            or not context.principal.app_installation_id
        ):
            raise ValidationError("Work handoff receipts require a delegated App credential")
        try:
            result = await asyncio.to_thread(
                self.store.add_work_handoff_receipt,
                value={
                    "receipt_id": f"handoffrcpt_{uuid4().hex}",
                    "handoff_id": handoff_id,
                    "owner_user_id": context.user_id,
                    "installation_id": context.principal.app_installation_id,
                    "idempotency_key": context.idempotency_key,
                    "created_by": context.principal.subject,
                    "audit_id": _audit_id(),
                    **value,
                },
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if result is None:
            raise NotFoundError("active Work handoff not found")
        return result

    async def cancel_handoff(
        self, context: RequestContext, handoff_id: str
    ) -> dict[str, Any]:
        if context.principal.app_client_id:
            raise ValidationError("delegated App credentials cannot cancel a Work handoff")
        value = await asyncio.to_thread(
            self.store.cancel_work_handoff,
            handoff_id,
            expected_user_id=context.user_id,
            actor_id=context.principal.subject,
            audit_id=_audit_id(),
        )
        if value is None:
            raise NotFoundError("active owner Work handoff not found")
        return value

    async def list_handoff_receipts(
        self, context: RequestContext, handoff_id: str
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.store.list_work_handoff_receipts,
            handoff_id,
            expected_user_id=context.user_id,
            installation_id=context.principal.app_installation_id,
        )

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
