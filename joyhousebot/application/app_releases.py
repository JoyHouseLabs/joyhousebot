"""App Package publication and per-user installation use cases."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from joyhousebot.application.app_manifest_validation import (
    AppManifestDependencyValidator,
)
from joyhousebot.application.errors import ConflictError, NotFoundError, ValidationError
from joyhousebot.domain.app_packages import (
    app_manifest_sha256,
    normalize_app_manifest,
    validate_install_configuration,
)
from joyhousebot.domain.entrypoints import (
    entrypoint_descriptor,
    split_public_entrypoint_id,
    validate_entrypoint_input,
)
from joyhousebot.scheduling.repository import ScheduleRepository
from joyhousebot.storage.contracts import RuntimeStores
from joyhousebot.utils.permissions import permission_granted


class AppReleaseService:
    def __init__(self, store: Any) -> None:
        self.store = store
        self.dependency_validator = AppManifestDependencyValidator(
            RuntimeStores.from_backend(store)
        )

    async def save_draft(self, manifest: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        try:
            normalized = normalize_app_manifest(manifest)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return await asyncio.to_thread(
            self.store.save_app_release,
            normalized,
            manifest_sha256=app_manifest_sha256(normalized),
            actor_id=actor_id,
        )

    async def validate(
        self,
        app_id: str,
        version: str,
        *,
        user_id: str,
        persist: bool = True,
    ) -> dict[str, Any]:
        release = await asyncio.to_thread(self.store.get_app_release, app_id, version)
        if release is None:
            raise NotFoundError("App release not found")
        report = await asyncio.to_thread(
            self._validate_manifest_dependencies,
            dict(release["manifest"]),
            user_id=user_id,
        )
        if persist and release["status"] == "draft":
            await asyncio.to_thread(
                self.store.record_app_validation,
                app_id,
                version,
                report,
            )
        return report

    async def publish(
        self, app_id: str, version: str, *, actor_id: str, user_id: str
    ) -> dict[str, Any]:
        report = await self.validate(app_id, version, user_id=user_id)
        if not report["valid"]:
            raise ConflictError(
                "App Package dependencies are not ready: " + "; ".join(report["errors"])
            )
        try:
            return await asyncio.to_thread(
                self.store.publish_app_release,
                app_id,
                version,
                actor_id=actor_id,
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc

    async def install(
        self,
        app_id: str,
        version: str,
        *,
        user_id: str,
        actor_id: str,
        configuration: dict[str, Any],
        granted_permissions: list[str],
    ) -> dict[str, Any]:
        release = await asyncio.to_thread(self.store.get_app_release, app_id, version)
        if release is None or release["status"] != "published":
            raise NotFoundError("published App release not found")
        manifest = dict(release["manifest"])
        declared_permissions = sorted(str(item) for item in manifest.get("permissions") or [])
        granted = sorted({str(item) for item in granted_permissions})
        if granted != declared_permissions:
            raise ValidationError(
                "granted_permissions must exactly match the App Package permission declaration"
            )
        try:
            validate_install_configuration(configuration)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        report = await self.validate(app_id, version, user_id=user_id, persist=False)
        if not report["valid"]:
            raise ConflictError(
                "App Package dependencies are not ready: " + "; ".join(report["errors"])
            )
        try:
            return await asyncio.to_thread(
                self.store.install_app_release,
                installation_id=f"appinst_{uuid4().hex}",
                user_id=user_id,
                app_id=app_id,
                version=version,
                configuration=dict(configuration),
                granted_permissions=granted,
                dependency_lock=dict(report["dependency_lock"]),
                actor_id=actor_id,
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc

    async def transition(
        self,
        installation_id: str,
        *,
        user_id: str,
        actor_id: str,
        action: str,
    ) -> dict[str, Any]:
        installation = await asyncio.to_thread(
            self.store.get_app_installation,
            installation_id,
            expected_user_id=user_id,
        )
        if installation is None:
            raise NotFoundError("App Package installation not found")
        check_version = (
            installation.get("previous_version")
            if action == "rollback"
            else installation["version"]
        )
        if action in {"activate", "rollback"}:
            if not check_version:
                raise ConflictError("App Package installation has no previous version")
            report = await self.validate(
                installation["app_id"],
                str(check_version),
                user_id=user_id,
                persist=False,
            )
            if not report["valid"]:
                raise ConflictError(
                    "App Package dependencies are not ready: " + "; ".join(report["errors"])
                )
        try:
            installation_row = await asyncio.to_thread(
                self.store.transition_app_installation,
                installation_id,
                user_id=user_id,
                action=action,
                actor_id=actor_id,
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        if action in {"disable", "uninstall", "rollback"}:
            await asyncio.to_thread(
                ScheduleRepository(self.store).set_enabled_by_installation,
                installation_id,
                False,
                now_ms=int(time.time() * 1000),
            )
        elif action == "activate":
            await asyncio.to_thread(
                ScheduleRepository(self.store).set_enabled_by_installation,
                installation_id,
                True,
                now_ms=int(time.time() * 1000),
            )
        return installation_row

    async def list_installed(
        self, *, user_id: str, active_only: bool = True
    ) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(self.store.list_app_installations, user_id=user_id)
        if active_only:
            rows = [row for row in rows if row.get("status") == "active"]
        return [self._public_installation(row) for row in rows]

    async def release(self, app_id: str, version: str) -> dict[str, Any]:
        release = await asyncio.to_thread(self.store.get_app_release, app_id, version)
        if release is None or release["status"] != "published":
            raise NotFoundError("published App release not found")
        return release

    async def get_installed(self, installation_id: str, *, user_id: str) -> dict[str, Any]:
        installation = await asyncio.to_thread(
            self.store.get_app_installation,
            installation_id,
            expected_user_id=user_id,
        )
        if installation is None:
            raise NotFoundError("App installation not found")
        return self._public_installation(installation)

    async def usage(
        self,
        installation_id: str,
        *,
        user_id: str,
        since: datetime | None,
        until: datetime | None,
    ) -> dict[str, Any]:
        resolved_until = until or datetime.now(timezone.utc)
        resolved_since = since or resolved_until - timedelta(days=30)
        if resolved_since.tzinfo is None or resolved_until.tzinfo is None:
            raise ValidationError("App usage period must include a timezone")
        resolved_since = resolved_since.astimezone(timezone.utc)
        resolved_until = resolved_until.astimezone(timezone.utc)
        if resolved_since >= resolved_until:
            raise ValidationError("App usage period is invalid")
        if resolved_until - resolved_since > timedelta(days=366):
            raise ValidationError("App usage period cannot exceed 366 days")
        value = await asyncio.to_thread(
            self.store.get_app_installation_usage,
            installation_id,
            user_id=user_id,
            since=resolved_since,
            until=resolved_until,
        )
        if value is None:
            raise NotFoundError("App installation not found")
        return value

    async def resolve_launch(
        self,
        installation_id: str,
        *,
        user_id: str,
        entrypoint_id: str | None,
        structured_input: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        installation = await asyncio.to_thread(
            self.store.get_app_installation,
            installation_id,
            expected_user_id=user_id,
        )
        if installation is None:
            raise NotFoundError("App installation not found")
        if installation.get("status") != "active":
            raise ConflictError("App installation must be active before it can launch Runs")
        if not any(
            permission_granted(item, "runs.submit")
            for item in installation.get("granted_permissions") or []
        ):
            raise ConflictError("App installation is not granted runs.submit")
        report = await self.validate(
            installation["app_id"],
            installation["version"],
            user_id=user_id,
            persist=False,
        )
        if not report["valid"]:
            raise ConflictError(
                "App dependencies are no longer execution-ready: " + "; ".join(report["errors"])
            )
        if dict(report["dependency_lock"]) != dict(installation["dependency_lock"]):
            raise ConflictError(
                "App dependency lock changed; reinstall or upgrade before launching"
            )
        entrypoints = list(dict(installation["manifest"]).get("entrypoints") or [])
        if not entrypoints:
            raise ConflictError("App release does not declare an executable entrypoint")
        selected = next(
            (
                item
                for item in entrypoints
                if (
                    str(item.get("entrypoint_id")) == str(entrypoint_id)
                    if entrypoint_id
                    else bool(item.get("default"))
                )
            ),
            None,
        )
        if selected is None:
            raise NotFoundError("App entrypoint not found")
        execution = dict(selected["execution"])
        pinned_revision_id = None
        if execution.get("mode") in {"agent", "team"}:
            pinned_revision_id = str(execution.pop("revision_id"))
        elif execution.get("mode") == "scenario":
            pinned_revision_id = str(execution.pop("agent_revision_id"))
        if execution.get("mode") == "scenario":
            execution["inputs"] = {
                **dict(execution.get("inputs") or {}),
                **dict(structured_input or {}),
            }
        metadata = {
            "app": {
                "installation_id": installation_id,
                "app_id": installation["app_id"],
                "version": installation["version"],
                "manifest_sha256": installation["manifest_sha256"],
                "entrypoint_id": selected["entrypoint_id"],
            }
        }
        return dict(selected), {
            "execution": execution,
            "metadata": metadata,
            "pinned_revision_id": pinned_revision_id,
        }

    async def list_entrypoints(
        self,
        *,
        user_id: str,
        installation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        installations = await self.list_installed(user_id=user_id, active_only=True)
        if installation_id:
            installations = [
                item for item in installations if item["installation_id"] == installation_id
            ]
        return [
            entrypoint_descriptor(installation, entrypoint)
            for installation in installations
            for entrypoint in installation.get("entrypoints") or []
        ]

    async def resolve_entrypoint(
        self,
        public_id: str,
        *,
        user_id: str,
        expected_installation_id: str | None,
        structured_input: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            installation_id, entrypoint_id = split_public_entrypoint_id(public_id)
        except ValueError as exc:
            raise NotFoundError("EntryPoint not found") from exc
        if expected_installation_id and installation_id != expected_installation_id:
            raise NotFoundError("EntryPoint not found")
        entrypoint, launch = await self.resolve_launch(
            installation_id,
            user_id=user_id,
            entrypoint_id=entrypoint_id,
            structured_input=structured_input,
        )
        installation = await self.get_installed(installation_id, user_id=user_id)
        descriptor = entrypoint_descriptor(installation, entrypoint)
        try:
            validate_entrypoint_input(descriptor["input_schema"], structured_input)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        launch["entrypoint_policy"] = {
            "interaction_mode": entrypoint["interaction_mode"],
            "output_schema": entrypoint.get("output_schema"),
            "verification_policy": entrypoint.get("verification_policy") or {},
            "timeout_seconds": entrypoint["timeout_seconds"],
        }
        return descriptor, launch

    def _validate_manifest_dependencies(
        self, manifest: dict[str, Any], *, user_id: str
    ) -> dict[str, Any]:
        return self.dependency_validator.validate(manifest, user_id=user_id)

    @staticmethod
    def _public_installation(value: dict[str, Any]) -> dict[str, Any]:
        manifest = dict(value.get("manifest") or {})
        return {
            "installation_id": value["installation_id"],
            "app_id": value["app_id"],
            "version": value["version"],
            "name": value["name"],
            "description": value["description"],
            "status": value["status"],
            "entrypoints": list(manifest.get("entrypoints") or []),
            "granted_permissions": list(value.get("granted_permissions") or []),
            "work_consumers": list(manifest.get("work_consumers") or []),
            "ui": dict(manifest.get("ui") or {}),
            "manifest_sha256": value["manifest_sha256"],
            "updated_at": value["updated_at"],
        }
