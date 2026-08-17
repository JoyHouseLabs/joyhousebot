"""Independent, immutable Skill asset contracts.

A Skill is reusable instructional knowledge bound to an Agent or Workflow.  It
is not an executable Capability and therefore has no adapter or plugin runtime
identity.  Published versions are addressed by ``skill_id + version + digest``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

_SKILL_ID = re.compile(r"^skill\.[A-Za-z0-9][A-Za-z0-9_.:-]{0,121}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")


@dataclass(frozen=True, slots=True)
class SkillRef:
    """An immutable reference to published Skill content."""

    skill_id: str
    version: str
    content_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "skill_id", normalize_skill_id(self.skill_id))
        object.__setattr__(self, "version", normalize_skill_version(self.version))
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.content_sha256):
            raise ValueError("Skill reference must pin a sha256 content digest")

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.skill_id, self.version, self.content_sha256

    def to_dict(self) -> dict[str, str]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "content_sha256": self.content_sha256,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SkillRef":
        return cls(
            skill_id=str(value["skill_id"]),
            version=str(value["version"]),
            content_sha256=str(value["content_sha256"]),
        )


def normalize_skill_id(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SKILL_ID.fullmatch(normalized):
        raise ValueError(
            "skill_id must start with 'skill.' and contain only letters, numbers, . _ : -"
        )
    return normalized


def normalize_skill_version(value: str) -> str:
    normalized = str(value or "").strip()
    if not _VERSION.fullmatch(normalized):
        raise ValueError("Skill version is required and must be a stable version identifier")
    return normalized


def _skill_text_fields(value: dict[str, Any]) -> tuple[str, str, str]:
    name = str(value.get("name") or "").strip()
    description = str(value.get("description") or "").strip()
    instruction_content = str(value.get("instruction_content") or "").strip()
    if not name or len(name) > 160:
        raise ValueError("Skill name is required and must be <= 160 characters")
    if len(description) > 2000:
        raise ValueError("Skill description must be <= 2000 characters")
    if len(instruction_content) > 200_000:
        raise ValueError("Skill instruction content must be <= 200000 characters")
    return name, description, instruction_content


def _skill_tags(value: Any) -> list[str]:
    tags = sorted({str(item).strip().lower() for item in value or [] if str(item).strip()})
    if len(tags) > 32 or any(len(item) > 64 for item in tags):
        raise ValueError("Skill tags support at most 32 values of 64 characters")
    return tags


def _skill_capabilities(value: Any) -> list[dict[str, str]]:
    capabilities: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in value or []:
        if not isinstance(raw, dict):
            raise ValueError("required_capabilities entries must be objects")
        capability_id = str(raw.get("capability_id") or "").strip()
        version = str(raw.get("version") or "").strip()
        if not capability_id or not version:
            raise ValueError("required Capabilities must pin capability_id and version")
        identity = (capability_id, version)
        if identity not in seen:
            seen.add(identity)
            capabilities.append({"capability_id": capability_id, "version": version})
    return capabilities


def _skill_integrations(value: Any) -> list[str]:
    integrations = sorted({str(item).strip() for item in value or [] if str(item).strip()})
    if len(integrations) > 32:
        raise ValueError("Skill supports at most 32 required Integrations")
    return integrations


def _skill_schema(value: Any, *, field: str) -> dict[str, Any]:
    schema = value or {}
    if not isinstance(schema, dict):
        raise ValueError(f"{field} must be a JSON object")
    try:
        if schema:
            Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"invalid Skill {field}: {exc.message}") from exc
    return dict(schema)


def _skill_eval_cases(value: Any) -> list[dict[str, Any]]:
    cases = _object_list(value, field="eval_cases", maximum=64)
    for index, case in enumerate(cases):
        if not str(case.get("name") or "").strip():
            raise ValueError(f"eval_cases[{index}].name is required")
        if not str(case.get("expected_behavior") or "").strip():
            raise ValueError(f"eval_cases[{index}].expected_behavior is required")
    return cases


def normalize_skill_document(value: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical draft/published document without mutable status fields."""
    skill_id = normalize_skill_id(str(value.get("skill_id") or ""))
    version = normalize_skill_version(str(value.get("version") or ""))
    name, description, instruction_content = _skill_text_fields(value)
    tags = _skill_tags(value.get("tags"))
    required_capabilities = _skill_capabilities(value.get("required_capabilities"))
    required_integrations = _skill_integrations(value.get("required_integrations"))
    input_schema = _skill_schema(value.get("input_schema"), field="input_schema")
    output_schema = _skill_schema(value.get("output_schema"), field="output_schema")
    examples = _object_list(value.get("examples"), field="examples", maximum=24)
    eval_cases = _skill_eval_cases(value.get("eval_cases"))
    templates = _object_list(value.get("templates"), field="templates", maximum=24)

    return {
        "skill_id": skill_id,
        "version": version,
        "name": name,
        "description": description,
        "instruction_content": instruction_content,
        "tags": tags,
        "input_schema": input_schema,
        "output_schema": output_schema,
        "required_capabilities": required_capabilities,
        "required_integrations": required_integrations,
        "examples": examples,
        "eval_cases": eval_cases,
        "templates": templates,
        "change_note": str(value.get("change_note") or "").strip()[:2000],
        "source": dict(value.get("source") or {}),
    }


def skill_content_sha256(document: dict[str, Any]) -> str:
    canonical = normalize_skill_document(document)
    payload = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{sha256(payload).hexdigest()}"


def validate_skill_document(
    document: dict[str, Any],
    *,
    available_capabilities: set[tuple[str, str]] | None = None,
    available_integrations: set[str] | None = None,
) -> dict[str, Any]:
    """Run deterministic publication checks and return auditable evidence."""
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    try:
        normalized = normalize_skill_document(document)
    except ValueError as exc:
        return {
            "valid": False,
            "errors": [str(exc)],
            "warnings": [],
            "checks": [{"check": "document_schema", "passed": False}],
        }

    instruction = normalized["instruction_content"]
    content_ok = len(instruction) >= 40
    checks.append({"check": "instruction_content", "passed": content_ok})
    if not content_ok:
        errors.append("Skill instruction content must contain at least 40 characters")

    eval_cases = normalized["eval_cases"]
    checks.append({"check": "eval_cases", "passed": bool(eval_cases), "count": len(eval_cases)})
    if not eval_cases:
        warnings.append("No Eval case is defined; publication has only structural evidence")

    required_capabilities = {
        (item["capability_id"], item["version"])
        for item in normalized["required_capabilities"]
    }
    missing_capabilities = sorted(required_capabilities - (available_capabilities or set()))
    capability_ok = available_capabilities is None or not missing_capabilities
    checks.append(
        {
            "check": "required_capabilities",
            "passed": capability_ok,
            "missing": [f"{item[0]}@{item[1]}" for item in missing_capabilities],
        }
    )
    if not capability_ok:
        errors.append(
            "Required Capability versions are not published: "
            + ", ".join(f"{item[0]}@{item[1]}" for item in missing_capabilities)
        )

    required_integrations = set(normalized["required_integrations"])
    missing_integrations = sorted(required_integrations - (available_integrations or set()))
    integration_ok = available_integrations is None or not missing_integrations
    checks.append(
        {
            "check": "required_integrations",
            "passed": integration_ok,
            "missing": missing_integrations,
        }
    )
    if not integration_ok:
        errors.append(
            "Required Integrations are not published: " + ", ".join(missing_integrations)
        )

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "content_sha256": skill_content_sha256(normalized),
    }


def public_skill_ref(version: dict[str, Any]) -> dict[str, str]:
    return {
        "skill_id": str(version["skill_id"]),
        "version": str(version["version"]),
        "content_sha256": str(version["content_sha256"]),
    }


def _object_list(value: Any, *, field: str, maximum: int) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} must be an array with at most {maximum} entries")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{field} entries must be objects")
    return [dict(item) for item in value]
