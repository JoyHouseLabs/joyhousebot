"""App Pack publication and per-user installation use cases."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from joyhousebot import __version__
from joyhousebot.application.errors import ConflictError, NotFoundError, ValidationError
from joyhousebot.domain.app_packs import (
    app_manifest_sha256,
    normalize_app_manifest,
    validate_install_configuration,
)
from joyhousebot.domain.capabilities.models import CapabilityRef
from joyhousebot.utils.permissions import permission_granted


class AppPackService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def save_draft(
        self, manifest: dict[str, Any], *, actor_id: str
    ) -> dict[str, Any]:
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
            raise NotFoundError("App Pack release not found")
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
            raise ConflictError("App Pack dependencies are not ready: " + "; ".join(report["errors"]))
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
            raise NotFoundError("published App Pack release not found")
        manifest = dict(release["manifest"])
        declared_permissions = sorted(str(item) for item in manifest.get("permissions") or [])
        granted = sorted({str(item) for item in granted_permissions})
        if granted != declared_permissions:
            raise ValidationError(
                "granted_permissions must exactly match the App Pack permission declaration"
            )
        try:
            validate_install_configuration(configuration)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        report = await self.validate(app_id, version, user_id=user_id, persist=False)
        if not report["valid"]:
            raise ConflictError("App Pack dependencies are not ready: " + "; ".join(report["errors"]))
        try:
            return await asyncio.to_thread(
                self.store.install_app_pack,
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
            raise NotFoundError("App Pack installation not found")
        check_version = (
            installation.get("previous_version")
            if action == "rollback"
            else installation["version"]
        )
        if action in {"activate", "rollback"}:
            if not check_version:
                raise ConflictError("App Pack installation has no previous version")
            report = await self.validate(
                installation["app_id"],
                str(check_version),
                user_id=user_id,
                persist=False,
            )
            if not report["valid"]:
                raise ConflictError(
                    "App Pack dependencies are not ready: " + "; ".join(report["errors"])
                )
        try:
            return await asyncio.to_thread(
                self.store.transition_app_installation,
                installation_id,
                user_id=user_id,
                action=action,
                actor_id=actor_id,
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc

    async def list_installed(self, *, user_id: str, active_only: bool = True) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(self.store.list_app_installations, user_id=user_id)
        if active_only:
            rows = [row for row in rows if row.get("status") == "active"]
        return [self._public_installation(row) for row in rows]

    async def get_installed(
        self, installation_id: str, *, user_id: str
    ) -> dict[str, Any]:
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
        scenario_inputs: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Resolve one active App entrypoint to the existing public Run authority."""
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
                **dict(scenario_inputs or {}),
            }
        elif scenario_inputs:
            raise ValidationError("inputs are supported only by Scenario App entrypoints")
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

    def _validate_manifest_dependencies(
        self, manifest: dict[str, Any], *, user_id: str
    ) -> dict[str, Any]:
        errors: list[str] = []
        checks: list[dict[str, Any]] = []
        lock: dict[str, Any] = {
            "extensions": [],
            "capabilities": [],
            "agents": [],
            "teams": [],
            "skills": [],
            "workflows": [],
            "scenarios": [],
            "integrations": [],
        }

        core = dict(manifest.get("core") or {})
        minimum = str(core.get("min_version") or "")
        maximum = str(core.get("max_version") or "")
        core_valid = (not minimum or _version_key(__version__) >= _version_key(minimum)) and (
            not maximum or _version_key(__version__) <= _version_key(maximum)
        )
        core_reference = {
            "runtime_version": __version__,
            "min_version": minimum,
            "max_version": maximum,
        }
        self._check(checks, errors, "core", core_reference, core_valid)
        lock["core"] = core_reference

        for reference in manifest.get("extensions") or []:
            release = self.store.get_plugin_release(
                reference["extension_id"], reference["version"]
            )
            valid = bool(
                release
                and release["status"] == "active"
                and release["build_digest"] == reference["build_digest"]
            )
            self._check(checks, errors, "extension", reference, valid)
            if valid:
                lock["extensions"].append(dict(reference))

        for raw in manifest.get("capabilities") or []:
            reference = CapabilityRef.from_dict(dict(raw))
            definition = self.store.get_capability_definition(
                reference.capability_id, reference.version
            )
            valid = bool(
                definition
                and CapabilityRef.from_dict(dict(definition["ref"])).identity
                == reference.identity
                and self.store.is_plugin_execution_enabled(reference.plugin_id)
            )
            self._check(checks, errors, "capability", raw, valid)
            if valid:
                lock["capabilities"].append(reference.to_dict())

        assets = dict(manifest.get("assets") or {})
        for reference in assets.get("agents") or []:
            revision = self.store.get_agent_revision(reference["revision_id"])
            definition = self.store.get_agent_definition(reference["agent_id"])
            valid = bool(
                revision
                and definition
                and revision.agent_id == reference["agent_id"]
                and revision.status == "published"
                and definition.status == "active"
            )
            self._check(checks, errors, "agent", reference, valid)
            if valid:
                lock["agents"].append(dict(reference))

        for reference in assets.get("teams") or []:
            team = self.store.get_agent_team_revision(reference["revision_id"])
            valid = bool(
                team
                and team.team_id == reference["team_id"]
                and team.status == "published"
            )
            self._check(checks, errors, "team", reference, valid)
            if valid:
                lock["teams"].append(dict(reference))

        for reference in assets.get("skills") or []:
            skill = self.store.get_published_skill(reference["skill_id"], reference["version"])
            valid = bool(
                skill and skill.get("content_sha256") == reference["content_sha256"]
            )
            self._check(checks, errors, "skill", reference, valid)
            if valid:
                lock["skills"].append(dict(reference))

        for reference in assets.get("scenarios") or []:
            scenario = self.store.get_scenario_version(
                reference["scenario_id"], int(reference["version"])
            )
            valid = bool(
                scenario
                and scenario.status == "published"
            )
            self._check(checks, errors, "scenario", reference, valid)
            if valid:
                lock["scenarios"].append(dict(reference))

        for reference in assets.get("workflows") or []:
            workflow = self.store.get_user_workflow(
                reference["workflow_id"], expected_user_id=user_id
            )
            revisions = (
                self.store.list_user_workflow_revisions(
                    reference["workflow_id"], user_id=user_id
                )
                if workflow
                else []
            )
            revision = next(
                (
                    item
                    for item in revisions
                    if item["revision_id"] == reference["revision_id"]
                ),
                None,
            )
            valid = bool(
                workflow
                and revision
                and revision.get("status") in {"published", "superseded"}
            )
            self._check(checks, errors, "workflow", reference, valid)
            if valid:
                lock["workflows"].append(dict(reference))

        for connection_id in manifest.get("integrations") or []:
            connection = self.store.get_remote_connection(connection_id)
            revision = dict((connection or {}).get("current_revision") or {})
            valid = bool(connection and revision.get("status") == "published")
            reference = {
                "connection_id": connection_id,
                "revision_id": revision.get("revision_id"),
                "fingerprint": revision.get("fingerprint"),
            }
            self._check(checks, errors, "integration", reference, valid)
            if valid:
                lock["integrations"].append(reference)

        for entrypoint in manifest.get("entrypoints") or []:
            execution = dict(entrypoint["execution"])
            mode = str(execution["mode"])
            if mode == "agent":
                valid = any(
                    item["agent_id"] == execution["agent_id"]
                    and item["revision_id"] == execution["revision_id"]
                    for item in lock["agents"]
                )
            elif mode == "team":
                valid = any(
                    item["team_id"] == execution["team_id"]
                    and item["revision_id"] == execution["revision_id"]
                    for item in lock["teams"]
                )
            elif mode == "scenario":
                valid = any(
                    item["scenario_id"] == execution["scenario_id"]
                    and int(item["version"]) == int(execution["version"])
                    for item in lock["scenarios"]
                )
                valid = valid and any(
                    item["agent_id"] == execution["agent_id"]
                    and item["revision_id"] == execution["agent_revision_id"]
                    for item in lock["agents"]
                )
            else:
                valid = any(
                    item["workflow_id"] == execution["workflow_id"]
                    and item["revision_id"] == execution["revision_id"]
                    for item in lock["workflows"]
                )
            self._check(
                checks,
                errors,
                "entrypoint",
                {"entrypoint_id": entrypoint["entrypoint_id"], "execution": execution},
                valid,
            )

        return {
            "valid": not errors,
            "errors": errors,
            "checks": checks,
            "dependency_lock": lock,
        }

    @staticmethod
    def _check(
        checks: list[dict[str, Any]],
        errors: list[str],
        kind: str,
        reference: Any,
        valid: bool,
    ) -> None:
        checks.append({"kind": kind, "reference": reference, "passed": valid})
        if not valid:
            errors.append(f"unavailable {kind}: {reference}")

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
            "work_consumers": list(manifest.get("work_consumers") or []),
            "ui": dict(manifest.get("ui") or {}),
            "manifest_sha256": value["manifest_sha256"],
            "updated_at": value["updated_at"],
        }


def _version_key(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(value).split(".")[:3])
    except ValueError as exc:
        raise ValidationError(f"App Pack Core version must be numeric: {value}") from exc
