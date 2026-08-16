"""Versioned App Pack contracts for distributable Porthouse applications.

An App Pack composes Core assets and Extension releases. It does not execute
inside the Runtime and cannot introduce another Run/Task state machine.
"""

from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Any

from porthouse.domain.capabilities.models import CapabilityRef
from porthouse.domain.skills import SkillRef
from porthouse.market_protocol.canonical import canonical_sha256
from porthouse.market_protocol.release import (
    normalize_app_id,
    normalize_app_version,
    normalize_publisher_id,
)
from porthouse.utils.permissions import permission_granted

_ASSET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def normalize_app_manifest(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("App Pack manifest must be an object")
    schema_version = int(value.get("schema_version") or 1)
    if schema_version not in {1, 2}:
        raise ValueError("unsupported App Pack manifest schema_version")
    app_id = normalize_app_id(str(value.get("app_id") or ""))
    version = normalize_app_version(str(value.get("version") or ""))
    name = str(value.get("name") or "").strip()
    description = str(value.get("description") or "").strip()
    publisher = str(value.get("publisher") or "").strip()
    if not name or len(name) > 160:
        raise ValueError("App Pack name is required and must be <= 160 characters")
    if len(description) > 4000 or len(publisher) > 160:
        raise ValueError("App Pack description or publisher is too long")

    extensions = _normalize_extensions(value.get("extensions"))
    capabilities = _normalize_capabilities(value.get("capabilities"))
    assets = _normalize_assets(value.get("assets"))
    integrations = _string_list(value.get("integrations"), field="integrations", maximum=64)
    permissions = _string_list(value.get("permissions"), field="permissions", maximum=128)
    secrets = _normalize_secrets(value.get("secrets"))
    triggers = _object_list(value.get("triggers"), field="triggers", maximum=64)
    evaluations = _object_list(value.get("evaluations"), field="evaluations", maximum=64)
    entrypoints = _normalize_entrypoints(value.get("entrypoints"))
    work_consumers = _normalize_work_consumers(value.get("work_consumers"))
    if entrypoints and not any(
        permission_granted(item, "runs.submit") for item in permissions
    ):
        raise ValueError("executable App entrypoints require the runs.submit permission")

    result = {
        "schema_version": schema_version,
        "app_id": app_id,
        "version": version,
        "name": name,
        "description": description,
        "publisher": publisher,
        "core": _normalize_core(value.get("core")),
        "extensions": extensions,
        "capabilities": capabilities,
        "assets": assets,
        "integrations": integrations,
        "permissions": permissions,
        "secrets": secrets,
        "triggers": triggers,
        "evaluations": evaluations,
        "configuration_schema": _object(value.get("configuration_schema")),
        "ui": _object(value.get("ui")),
        "metadata": _object(value.get("metadata")),
    }
    # Keep manifests that predate executable App entrypoints byte-for-byte
    # canonical compatible.  New manifests opt in by declaring at least one
    # entrypoint; an installed App without one remains a control-plane bundle
    # and cannot be launched from the public App data plane.
    if entrypoints:
        result["entrypoints"] = entrypoints
    if work_consumers:
        result["work_consumers"] = work_consumers
    if schema_version == 2:
        result.update(
            {
                "publisher_id": normalize_publisher_id(str(value.get("publisher_id") or "")),
                "licenses": _object(value.get("licenses")),
                "evidence": _object(value.get("evidence")),
                "data_practices": _normalize_data_practices(value.get("data_practices")),
                "metering": _normalize_metering(value.get("metering")),
            }
        )
    return result


def app_manifest_sha256(value: dict[str, Any]) -> str:
    normalized = normalize_app_manifest(value)
    if int(normalized["schema_version"]) == 2:
        return canonical_sha256(normalized)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(payload).hexdigest()}"


def _normalize_data_practices(value: Any) -> dict[str, Any]:
    source = _object(value)
    domains = _string_list(source.get("outbound_domains"), field="data_practices.outbound_domains", maximum=128)
    telemetry = str(source.get("telemetry") or "none")
    if telemetry not in {"none", "billing_only", "opt_in_analytics"}:
        raise ValueError("data_practices.telemetry is invalid")
    return {
        "telemetry": telemetry,
        "outbound_domains": domains,
        "collects_personal_data": bool(source.get("collects_personal_data", False)),
        "retention_days": max(0, min(int(source.get("retention_days") or 0), 36500)),
    }


def _normalize_metering(value: Any) -> list[dict[str, Any]]:
    rows = _object_list(value, field="metering", maximum=64)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        meter_id = _stable_id(row.get("meter_id"), field="meter_id")
        unit = str(row.get("unit") or "").strip()
        if not unit or len(unit) > 64:
            raise ValueError("meter unit is required and must be <= 64 characters")
        if meter_id in seen:
            raise ValueError(f"duplicate meter declaration: {meter_id}")
        seen.add(meter_id)
        result.append(
            {
                "meter_id": meter_id,
                "unit": unit,
                "description": str(row.get("description") or "")[:500],
                "source_event": str(row.get("source_event") or "")[:160],
            }
        )
    return sorted(result, key=lambda item: item["meter_id"])


def _normalize_entrypoints(value: Any) -> list[dict[str, Any]]:
    rows = _object_list(value, field="entrypoints", maximum=32)
    if not rows:
        return []
    result: list[dict[str, Any]] = []
    identities: set[str] = set()
    default_count = 0
    for row in rows:
        unknown = set(row) - {
            "entrypoint_id",
            "name",
            "description",
            "default",
            "execution",
            "interaction_mode",
            "timeout_seconds",
            "output_schema",
            "verification_policy",
        }
        if unknown:
            raise ValueError(f"App entrypoint contains unsupported fields: {sorted(unknown)}")
        entrypoint_id = _stable_id(row.get("entrypoint_id"), field="entrypoint_id")
        if entrypoint_id in identities:
            raise ValueError(f"duplicate App entrypoint: {entrypoint_id}")
        identities.add(entrypoint_id)
        is_default = bool(row.get("default", len(rows) == 1))
        default_count += int(is_default)
        interaction_mode = str(row.get("interaction_mode") or "auto").strip()
        if interaction_mode not in {"auto", "interactive", "background"}:
            raise ValueError("App entrypoint interaction_mode is invalid")
        timeout_seconds = float(row.get("timeout_seconds") or 300.0)
        if not 0 < timeout_seconds <= 3600:
            raise ValueError("App entrypoint timeout_seconds must be between 0 and 3600")
        result.append(
            {
                "entrypoint_id": entrypoint_id,
                "name": str(row.get("name") or entrypoint_id).strip()[:160],
                "description": str(row.get("description") or "").strip()[:1000],
                "default": is_default,
                "execution": _normalize_entrypoint_execution(row.get("execution")),
                "interaction_mode": interaction_mode,
                "timeout_seconds": timeout_seconds,
                "output_schema": (
                    _object(row.get("output_schema"))
                    if row.get("output_schema") is not None
                    else None
                ),
                "verification_policy": _object(row.get("verification_policy")),
            }
        )
    if default_count != 1:
        raise ValueError("App entrypoints must declare exactly one default")
    return sorted(result, key=lambda item: item["entrypoint_id"])


def _normalize_entrypoint_execution(value: Any) -> dict[str, Any]:
    source = _object(value)
    mode = str(source.get("mode") or "").strip()
    if mode == "agent":
        allowed = {"mode", "agent_id", "revision_id"}
        result = {
            "mode": mode,
            "agent_id": _stable_id(source.get("agent_id"), field="agent_id"),
            "revision_id": _stable_id(source.get("revision_id"), field="revision_id"),
        }
    elif mode == "team":
        allowed = {"mode", "team_id", "revision_id"}
        result = {
            "mode": mode,
            "team_id": _stable_id(source.get("team_id"), field="team_id"),
            "revision_id": _stable_id(source.get("revision_id"), field="revision_id"),
        }
    elif mode == "scenario":
        allowed = {
            "mode",
            "scenario_id",
            "version",
            "agent_id",
            "agent_revision_id",
            "inputs",
        }
        version = int(source.get("version") or 0)
        if version <= 0:
            raise ValueError("App Scenario entrypoint requires a positive version")
        result = {
            "mode": mode,
            "scenario_id": _stable_id(source.get("scenario_id"), field="scenario_id"),
            "version": version,
            "agent_id": _stable_id(source.get("agent_id") or "default", field="agent_id"),
            "agent_revision_id": _stable_id(
                source.get("agent_revision_id"), field="agent_revision_id"
            ),
            "inputs": _object(source.get("inputs")),
        }
    elif mode == "workflow":
        allowed = {"mode", "workflow_id", "revision_id"}
        result = {
            "mode": mode,
            "workflow_id": _stable_id(source.get("workflow_id"), field="workflow_id"),
            "revision_id": _stable_id(source.get("revision_id"), field="revision_id"),
        }
    else:
        raise ValueError("App entrypoint execution mode must be agent, team, scenario, or workflow")
    unknown = set(source) - allowed
    if unknown:
        raise ValueError(
            f"App {mode} entrypoint execution contains unsupported fields: {sorted(unknown)}"
        )
    return result


def _normalize_work_consumers(value: Any) -> list[dict[str, Any]]:
    """Normalize explicit declarations for Apps that can receive a Work version.

    A consumer is a capability declaration, not an implicit entitlement to read
    the user's whole library. Runtime matches one Work to one installed App and
    issues a separately auditable, version-pinned handoff.
    """
    rows = _object_list(value, field="work_consumers", maximum=32)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    classifications = {"public", "internal", "confidential", "restricted"}
    for row in rows:
        unknown = set(row) - {
            "consumer_id",
            "name",
            "description",
            "purposes",
            "media_types",
            "max_data_classification",
            "input_schema",
        }
        if unknown:
            raise ValueError(f"Work consumer contains unsupported fields: {sorted(unknown)}")
        consumer_id = _stable_id(row.get("consumer_id"), field="consumer_id")
        if consumer_id in seen:
            raise ValueError(f"duplicate Work consumer: {consumer_id}")
        seen.add(consumer_id)
        purposes = _string_list(row.get("purposes"), field="work_consumer.purposes", maximum=32)
        media_types = _string_list(
            row.get("media_types"), field="work_consumer.media_types", maximum=32
        )
        if not purposes or not media_types:
            raise ValueError("Work consumer requires purposes and media_types")
        maximum = str(row.get("max_data_classification") or "internal")
        if maximum not in classifications:
            raise ValueError("Work consumer max_data_classification is invalid")
        name = str(row.get("name") or consumer_id).strip()
        if not name or len(name) > 160:
            raise ValueError("Work consumer name must be between 1 and 160 characters")
        result.append(
            {
                "consumer_id": consumer_id,
                "name": name,
                "description": str(row.get("description") or "").strip()[:1000],
                "purposes": purposes,
                "media_types": media_types,
                "max_data_classification": maximum,
                "input_schema": _object(row.get("input_schema")),
            }
        )
    return sorted(result, key=lambda item: item["consumer_id"])


def validate_install_configuration(value: Any) -> None:
    """Reject embedded secrets; settings may only carry env:// references."""
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("password", "secret", "token", "api_key")):
                if item not in (None, "") and not str(item).startswith("env://"):
                    raise ValueError(f"sensitive App setting {key!r} must use env://VARIABLE")
            validate_install_configuration(item)
    elif isinstance(value, list):
        for item in value:
            validate_install_configuration(item)


def _normalize_core(value: Any) -> dict[str, str]:
    source = _object(value)
    return {
        "min_version": str(source.get("min_version") or "").strip(),
        "max_version": str(source.get("max_version") or "").strip(),
    }


def _normalize_extensions(value: Any) -> list[dict[str, str]]:
    rows = _object_list(value, field="extensions", maximum=64)
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        extension_id = _stable_id(row.get("extension_id"), field="extension_id")
        version = normalize_app_version(str(row.get("version") or ""))
        digest = str(row.get("build_digest") or "")
        if not _DIGEST.fullmatch(digest):
            raise ValueError("Extension references must pin a sha256 build_digest")
        identity = (extension_id, version, digest)
        if identity not in seen:
            seen.add(identity)
            result.append(
                {"extension_id": extension_id, "version": version, "build_digest": digest}
            )
    return result


def _normalize_capabilities(value: Any) -> list[dict[str, Any]]:
    rows = _object_list(value, field="capabilities", maximum=128)
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        reference = CapabilityRef.from_dict(row)
        if reference.kind.value not in {"tool", "connector"}:
            raise ValueError("App Packs may require only executable Tool/Connector capabilities")
        identity = tuple(str(item) for item in reference.identity)
        if identity not in seen:
            seen.add(identity)
            result.append(reference.to_dict())
    return result


def _normalize_assets(value: Any) -> dict[str, list[dict[str, Any]]]:
    source = _object(value)
    unknown = set(source) - {"agents", "teams", "skills", "workflows", "scenarios"}
    if unknown:
        raise ValueError(f"unknown App Pack asset groups: {sorted(unknown)}")
    skills = [SkillRef.from_dict(row).to_dict() for row in _object_list(
        source.get("skills"), field="assets.skills", maximum=128
    )]
    return {
        "agents": _versioned_assets(source.get("agents"), "agent_id", "revision_id"),
        "teams": _versioned_assets(source.get("teams"), "team_id", "revision_id"),
        "skills": skills,
        "workflows": _versioned_assets(
            source.get("workflows"), "workflow_id", "revision_id"
        ),
        "scenarios": _versioned_assets(source.get("scenarios"), "scenario_id", "version"),
    }


def _versioned_assets(value: Any, identity_key: str, revision_key: str) -> list[dict[str, Any]]:
    rows = _object_list(value, field=f"assets.{identity_key}", maximum=128)
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        identity = _stable_id(row.get(identity_key), field=identity_key)
        revision = str(row.get(revision_key) or "").strip()
        if not revision:
            raise ValueError(f"App Pack asset must pin {revision_key}")
        key = (identity, revision)
        if key in seen:
            continue
        seen.add(key)
        result.append({identity_key: identity, revision_key: row[revision_key]})
    return result


def _normalize_secrets(value: Any) -> list[dict[str, Any]]:
    rows = _object_list(value, field="secrets", maximum=64)
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", name):
            raise ValueError("App Pack secret names must be uppercase environment variable names")
        if name in names:
            raise ValueError(f"duplicate App Pack secret declaration: {name}")
        names.add(name)
        result.append(
            {
                "name": name,
                "required": bool(row.get("required", True)),
                "description": str(row.get("description") or "")[:500],
            }
        )
    return result


def _stable_id(value: Any, *, field: str) -> str:
    result = str(value or "").strip()
    if not _ASSET_ID.fullmatch(result):
        raise ValueError(f"{field} must be a stable identifier")
    return result


def _string_list(value: Any, *, field: str, maximum: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} must be an array with at most {maximum} entries")
    result = sorted({str(item).strip() for item in value if str(item).strip()})
    if any(len(item) > 256 for item in result):
        raise ValueError(f"{field} entries must be <= 256 characters")
    return result


def _object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("manifest field must be an object")
    return dict(value)


def _object_list(value: Any, *, field: str, maximum: int) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} must be an array with at most {maximum} entries")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{field} entries must be objects")
    return [dict(item) for item in value]
