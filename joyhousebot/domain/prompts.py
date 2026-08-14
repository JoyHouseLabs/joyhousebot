"""First-class, immutable Prompt assets for the Runtime control plane.

Prompt assets are deliberately narrower than Skills: they are reviewed text
policies which can be bound to a published Agent revision and frozen with a
Run.  A Prompt cannot execute tools, access private state, or claim hidden
provider reasoning.  Reusable operational know-how still belongs in Skills.
"""

from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

_PROMPT_ID = re.compile(r"^prompt\.[A-Za-z0-9][A-Za-z0-9_.:-]{0,120}$")
_TEMPLATE_VARIABLE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.-]{0,63})\s*}}")
_PROMPT_STATUSES = {"draft", "published", "retired"}


def normalize_prompt_id(value: str) -> str:
    prompt_id = str(value or "").strip().lower()
    if not _PROMPT_ID.fullmatch(prompt_id):
        raise ValueError(
            "prompt_id must start with 'prompt.' and contain only letters, numbers, . _ : -"
        )
    return prompt_id


def prompt_revision_id(prompt_id: str, version: int) -> str:
    return f"{normalize_prompt_id(prompt_id)}:v{int(version)}"


def normalize_prompt_document(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize a Prompt draft independent of mutable status."""
    prompt_id = normalize_prompt_id(str(value.get("prompt_id") or ""))
    version = int(value.get("version") or 0)
    name = str(value.get("name") or "").strip()
    description = str(value.get("description") or "").strip()
    content = str(value.get("content") or "").strip()
    if version < 1:
        raise ValueError("Prompt version must be >= 1")
    if not name or len(name) > 160:
        raise ValueError("Prompt name is required and must be <= 160 characters")
    if len(description) > 2_000:
        raise ValueError("Prompt description must be <= 2000 characters")
    if not content or len(content) > 200_000:
        raise ValueError("Prompt content is required and must be <= 200000 characters")

    input_schema = dict(value.get("input_schema") or {})
    output_contract = dict(value.get("output_contract") or {})
    for label, schema in (("input_schema", input_schema), ("output_contract", output_contract)):
        try:
            if schema:
                Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ValueError(f"invalid Prompt {label}: {exc.message}") from exc

    properties = set(dict(input_schema.get("properties") or {}))
    variables = sorted(set(_TEMPLATE_VARIABLE.findall(content)))
    missing = sorted(set(variables) - properties)
    if missing:
        raise ValueError(
            "Prompt template variables must be declared in input_schema.properties: "
            + ", ".join(missing)
        )
    # We must not turn a vendor's unavailable hidden reasoning into a product
    # contract. Authors may ask for concise rationale, evidence and decisions,
    # but not provider-private chain-of-thought.
    lowered = content.lower()
    forbidden = ("reveal your chain of thought", "show your chain-of-thought")
    if any(token in lowered for token in forbidden):
        raise ValueError("Prompt cannot request provider-private chain-of-thought")

    tags = sorted({str(item).strip().lower() for item in value.get("tags") or [] if str(item).strip()})
    if len(tags) > 32 or any(len(item) > 64 for item in tags):
        raise ValueError("Prompt tags support at most 32 values of 64 characters")
    return {
        "prompt_id": prompt_id,
        "revision_id": prompt_revision_id(prompt_id, version),
        "version": version,
        "name": name,
        "description": description,
        "content": content,
        "input_schema": input_schema,
        "output_contract": output_contract,
        "tags": tags,
        "change_note": str(value.get("change_note") or "").strip()[:2_000],
    }


def prompt_content_sha256(document: dict[str, Any]) -> str:
    normalized = normalize_prompt_document(document)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{sha256(payload).hexdigest()}"


def validate_prompt_document(document: dict[str, Any]) -> dict[str, Any]:
    """Return auditable structural evidence for a Prompt revision."""
    try:
        normalized = normalize_prompt_document(document)
    except ValueError as exc:
        return {"valid": False, "errors": [str(exc)], "checks": []}
    checks = [
        {"check": "content", "passed": len(normalized["content"]) >= 20},
        {"check": "template_variables_declared", "passed": True},
        {"check": "output_contract_schema", "passed": True},
        {"check": "no_private_reasoning_contract", "passed": True},
    ]
    errors = ["Prompt content must contain at least 20 characters"] if not checks[0]["passed"] else []
    return {
        "valid": not errors,
        "errors": errors,
        "checks": checks,
        "content_sha256": prompt_content_sha256(normalized),
        "template_variables": sorted(set(_TEMPLATE_VARIABLE.findall(normalized["content"]))),
    }


def bindable_prompt_content(document: dict[str, Any]) -> str:
    """Return static text safe for automatic Agent-revision binding.

    Dynamic prompts are supported as assets, but require an explicit caller to
    render and audit input values.  Silent substitution from unrelated Run
    metadata is deliberately forbidden.
    """
    normalized = normalize_prompt_document(document)
    variables = _TEMPLATE_VARIABLE.findall(normalized["content"])
    if variables:
        raise ValueError(
            "Prompt with template variables cannot be auto-bound; render it through an explicit Skill or App contract"
        )
    return normalized["content"]


def render_prompt_document(document: dict[str, Any], variables: dict[str, Any]) -> str:
    """Render declared ``{{variable}}`` placeholders for an explicit caller.

    Rendering is intentionally explicit: automatic Agent bindings always use
    static Prompt content, while Eval and App integrations supply their own
    bounded, auditable input values.
    """
    normalized = normalize_prompt_document(document)
    values = dict(variables or {})
    declared = set(dict(normalized["input_schema"].get("properties") or {}))
    unexpected = sorted(set(values) - declared)
    if unexpected:
        raise ValueError("Prompt variables are not declared: " + ", ".join(unexpected))
    required = set(normalized["input_schema"].get("required") or [])
    missing = sorted(required - set(values))
    if missing:
        raise ValueError("Prompt variables are required: " + ", ".join(missing))

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise ValueError(f"Prompt variable is missing: {name}")
        rendered = str(values[name])
        if len(rendered) > 20_000:
            raise ValueError(f"Prompt variable is too large: {name}")
        return rendered

    return _TEMPLATE_VARIABLE.sub(replace, normalized["content"])
