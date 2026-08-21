"""Explicit size limits for structured execution contracts."""

from __future__ import annotations

from typing import Any

from joyhousebot.domain.identity import canonical_json

MAX_STRUCTURED_CONTRACT_BYTES = 20_000


def structured_contract_json(
    value: dict[str, Any] | None,
    *,
    label: str,
) -> str | None:
    """Serialize a contract without truncation, failing before Run creation."""
    if not value:
        return None
    encoded = canonical_json(value)
    size = len(encoded.encode("utf-8"))
    if size > MAX_STRUCTURED_CONTRACT_BYTES:
        raise ValueError(
            f"{label} is {size} bytes; maximum is {MAX_STRUCTURED_CONTRACT_BYTES} bytes"
        )
    return encoded


def validate_execution_contracts(
    *,
    output_schema: dict[str, Any] | None,
    verification_policy: dict[str, Any] | None,
    prefix: str = "run",
) -> None:
    structured_contract_json(output_schema, label=f"{prefix} output_schema")
    structured_contract_json(
        verification_policy,
        label=f"{prefix} verification_policy",
    )


__all__ = [
    "MAX_STRUCTURED_CONTRACT_BYTES",
    "structured_contract_json",
    "validate_execution_contracts",
]
