"""Pure normalization for the fail-closed verification policy contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_TYPES = {"schema", "artifact", "deterministic"}


@dataclass(frozen=True, slots=True)
class VerifierSpec:
    verifier_id: str
    verifier_type: str
    version: str
    required: bool
    repairable: bool
    policy: dict[str, Any]


def normalize_verifiers(
    output_schema: dict[str, Any] | None,
    verification_policy: dict[str, Any] | None,
) -> tuple[VerifierSpec, ...]:
    raw_policy = dict(verification_policy or {})
    raw_verifiers = raw_policy.get("verifiers") or []
    if not isinstance(raw_verifiers, list):
        raise ValueError("verification_policy.verifiers must be an array")
    values: list[dict[str, Any]] = []
    if output_schema:
        values.append(
            {
                "id": "output-schema",
                "type": "schema",
                "schema": dict(output_schema),
                "required": True,
                "repairable": True,
            }
        )
    values.extend(dict(item) for item in raw_verifiers if isinstance(item, dict))
    if len(values) != len(raw_verifiers) + bool(output_schema):
        raise ValueError("each verification_policy verifier must be an object")
    specs: list[VerifierSpec] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        verifier_type = str(value.get("type") or "").strip().lower()
        if verifier_type not in _TYPES:
            raise ValueError(f"unsupported verifier type: {verifier_type or '<empty>'}")
        verifier_id = str(value.get("id") or f"{verifier_type}-{index + 1}").strip()
        if not verifier_id or len(verifier_id) > 128:
            raise ValueError("verifier id must contain 1-128 characters")
        if verifier_id in seen:
            raise ValueError(f"duplicate verifier id: {verifier_id}")
        seen.add(verifier_id)
        specs.append(
            VerifierSpec(
                verifier_id=verifier_id,
                verifier_type=verifier_type,
                version=str(value.get("version") or "1")[:64],
                required=bool(value.get("required", True)),
                repairable=bool(value.get("repairable", True)),
                policy=value,
            )
        )
    return tuple(specs)
