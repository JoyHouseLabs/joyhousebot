"""Read-only validation and immutable dependency locking for App manifests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from porthouse import __version__
from porthouse.application.errors import ValidationError
from porthouse.domain.capabilities.models import CapabilityRef
from porthouse.domain.collaboration_blueprints import normalize_collaboration_blueprint
from porthouse.storage.contracts import RuntimeStores


@dataclass(slots=True)
class AppDependencyReport:
    errors: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    lock: dict[str, Any] = field(
        default_factory=lambda: {
            "extensions": [],
            "capabilities": [],
            "agents": [],
            "teams": [],
            "skills": [],
            "workflows": [],
            "scenarios": [],
            "integrations": [],
        }
    )

    def check(
        self,
        kind: str,
        reference: Any,
        valid: bool,
        *,
        reason: str = "",
    ) -> None:
        self.checks.append({"kind": kind, "reference": reference, "passed": valid})
        if not valid:
            detail = f" ({reason})" if reason else ""
            self.errors.append(f"unavailable {kind}: {reference}{detail}")

    def result(self) -> dict[str, Any]:
        return {
            "valid": not self.errors,
            "errors": self.errors,
            "checks": self.checks,
            "dependency_lock": self.lock,
        }


class AppManifestDependencyValidator:
    """Resolve every declared App dependency to an executable published version."""

    def __init__(self, stores: RuntimeStores) -> None:
        self.stores = stores

    def validate(self, manifest: dict[str, Any], *, user_id: str) -> dict[str, Any]:
        report = AppDependencyReport()
        self._validate_core(manifest, report)
        self._validate_extensions(manifest, report)
        self._validate_capabilities(manifest, report)
        assets = dict(manifest.get("assets") or {})
        self._validate_agents(assets, report)
        self._validate_teams(assets, report)
        self._validate_skills(assets, report)
        self._validate_scenarios(assets, report)
        self._validate_workflows(assets, report, user_id=user_id)
        self._validate_integrations(manifest, report)
        self._validate_entrypoints(manifest, report)
        return report.result()

    @staticmethod
    def _validate_core(
        manifest: dict[str, Any], report: AppDependencyReport
    ) -> None:
        core = dict(manifest.get("core") or {})
        minimum = str(core.get("min_version") or "")
        maximum = str(core.get("max_version") or "")
        valid = (
            not minimum or _version_key(__version__) >= _version_key(minimum)
        ) and (not maximum or _version_key(__version__) <= _version_key(maximum))
        reference = {
            "runtime_version": __version__,
            "min_version": minimum,
            "max_version": maximum,
        }
        report.check("core", reference, valid)
        report.lock["core"] = reference

    def _validate_extensions(
        self, manifest: dict[str, Any], report: AppDependencyReport
    ) -> None:
        for reference in manifest.get("extensions") or []:
            release = self.stores.app_dependencies.get_plugin_release(
                reference["extension_id"], reference["version"]
            )
            valid = bool(
                release
                and release["status"] == "active"
                and release["build_digest"] == reference["build_digest"]
            )
            report.check("extension", reference, valid)
            if valid:
                report.lock["extensions"].append(dict(reference))

    def _validate_capabilities(
        self, manifest: dict[str, Any], report: AppDependencyReport
    ) -> None:
        for raw in manifest.get("capabilities") or []:
            reference = CapabilityRef.from_dict(dict(raw))
            definition = self.stores.catalog.get_capability_definition(
                reference.capability_id, reference.version
            )
            valid = bool(
                definition
                and CapabilityRef.from_dict(dict(definition["ref"])).identity
                == reference.identity
                and self.stores.app_dependencies.is_plugin_execution_enabled(
                    reference.plugin_id
                )
            )
            report.check("capability", raw, valid)
            if valid:
                report.lock["capabilities"].append(reference.to_dict())

    def _validate_agents(
        self, assets: dict[str, Any], report: AppDependencyReport
    ) -> None:
        for reference in assets.get("agents") or []:
            revision = self.stores.catalog.get_agent_revision(reference["revision_id"])
            definition = self.stores.catalog.get_agent_definition(reference["agent_id"])
            valid = bool(
                revision
                and definition
                and revision.agent_id == reference["agent_id"]
                and revision.status == "published"
                and definition.status == "active"
            )
            report.check("agent", reference, valid)
            if valid:
                report.lock["agents"].append(dict(reference))

    def _validate_teams(
        self, assets: dict[str, Any], report: AppDependencyReport
    ) -> None:
        for reference in assets.get("teams") or []:
            try:
                team = self.stores.catalog.get_agent_team_revision(
                    reference["revision_id"]
                )
            except ValueError as exc:
                report.check("team", reference, False, reason=str(exc))
                continue
            valid = bool(
                team
                and team.team_id == reference["team_id"]
                and team.status == "published"
            )
            report.check("team", reference, valid)
            if valid:
                report.lock["teams"].append(dict(reference))
            if team is not None and valid and team.collaboration_blueprint is not None:
                self._validate_team_blueprint(team, reference, report)

    @staticmethod
    def _validate_team_blueprint(
        team: Any, reference: dict[str, Any], report: AppDependencyReport
    ) -> None:
        try:
            normalize_collaboration_blueprint(
                team.collaboration_blueprint,
                member_ids={item.member_id for item in team.members},
                coordinator_member_id=team.coordinator_member_id,
                budget_policy=team.budget_policy,
            )
            valid = True
            reason = ""
        except ValueError as exc:
            valid = False
            reason = str(exc)
        report.check(
            "team_blueprint",
            {
                "team_id": reference["team_id"],
                "revision_id": reference["revision_id"],
            },
            valid,
            reason=reason,
        )

    def _validate_skills(
        self, assets: dict[str, Any], report: AppDependencyReport
    ) -> None:
        for reference in assets.get("skills") or []:
            skill = self.stores.catalog.get_published_skill(
                reference["skill_id"], reference["version"]
            )
            valid = bool(
                skill and skill.get("content_sha256") == reference["content_sha256"]
            )
            report.check("skill", reference, valid)
            if valid:
                report.lock["skills"].append(dict(reference))

    def _validate_scenarios(
        self, assets: dict[str, Any], report: AppDependencyReport
    ) -> None:
        for reference in assets.get("scenarios") or []:
            scenario = self.stores.scenarios.get_scenario_version(
                reference["scenario_id"], int(reference["version"])
            )
            valid = bool(scenario and scenario.status == "published")
            report.check("scenario", reference, valid)
            if valid:
                report.lock["scenarios"].append(dict(reference))

    def _validate_workflows(
        self,
        assets: dict[str, Any],
        report: AppDependencyReport,
        *,
        user_id: str,
    ) -> None:
        for reference in assets.get("workflows") or []:
            workflow = self.stores.workflows.get_user_workflow(
                reference["workflow_id"], expected_user_id=user_id
            )
            revisions = (
                self.stores.workflows.list_user_workflow_revisions(
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
            report.check("workflow", reference, valid)
            if valid:
                report.lock["workflows"].append(dict(reference))

    def _validate_integrations(
        self, manifest: dict[str, Any], report: AppDependencyReport
    ) -> None:
        for connection_id in manifest.get("integrations") or []:
            connection = self.stores.app_dependencies.get_remote_connection(connection_id)
            revision = dict((connection or {}).get("current_revision") or {})
            valid = bool(connection and revision.get("status") == "published")
            reference = {
                "connection_id": connection_id,
                "revision_id": revision.get("revision_id"),
                "fingerprint": revision.get("fingerprint"),
            }
            report.check("integration", reference, valid)
            if valid:
                report.lock["integrations"].append(reference)

    @staticmethod
    def _validate_entrypoints(
        manifest: dict[str, Any], report: AppDependencyReport
    ) -> None:
        for entrypoint in manifest.get("entrypoints") or []:
            execution = dict(entrypoint["execution"])
            valid = _entrypoint_available(execution, report.lock)
            report.check(
                "entrypoint",
                {
                    "entrypoint_id": entrypoint["entrypoint_id"],
                    "execution": execution,
                },
                valid,
            )


def _entrypoint_available(
    execution: dict[str, Any], lock: dict[str, Any]
) -> bool:
    mode = str(execution["mode"])
    if mode == "agent":
        return any(
            item["agent_id"] == execution["agent_id"]
            and item["revision_id"] == execution["revision_id"]
            for item in lock["agents"]
        )
    if mode == "team":
        return any(
            item["team_id"] == execution["team_id"]
            and item["revision_id"] == execution["revision_id"]
            for item in lock["teams"]
        )
    if mode == "scenario":
        scenario_available = any(
            item["scenario_id"] == execution["scenario_id"]
            and int(item["version"]) == int(execution["version"])
            for item in lock["scenarios"]
        )
        agent_available = any(
            item["agent_id"] == execution["agent_id"]
            and item["revision_id"] == execution["agent_revision_id"]
            for item in lock["agents"]
        )
        return scenario_available and agent_available
    return any(
        item["workflow_id"] == execution["workflow_id"]
        and item["revision_id"] == execution["revision_id"]
        for item in lock["workflows"]
    )


def _version_key(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(value).split(".")[:3])
    except ValueError as exc:
        raise ValidationError(
            f"App Pack Core version must be numeric: {value}"
        ) from exc


__all__ = ["AppManifestDependencyValidator"]
