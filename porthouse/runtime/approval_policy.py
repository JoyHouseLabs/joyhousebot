"""Deterministic approval policy for capability side effects."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from porthouse.runtime.narrative import redact_runtime_value

_HIGH_RISK_MARKERS = frozenset(
    {
        "delete",
        "destructive",
        "exec",
        "pay",
        "payment",
        "permission",
        "publish",
        "transfer",
    }
)


@dataclass(frozen=True, slots=True)
class ApprovalPolicy:
    required: bool
    mode: str
    required_role: str
    risk: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def capability_approval_policy(definition: Any) -> ApprovalPolicy:
    """Return policy from immutable capability safety metadata."""
    side_effect = str(definition.side_effect or "unknown").strip().lower()
    if side_effect in {"none", "read", "internal"}:
        return ApprovalPolicy(False, "auto", "none", "low", "no external side effect")

    markers = {
        side_effect,
        str(definition.ref.capability_id).lower(),
        *(str(item).lower() for item in definition.tags),
        *(str(item).lower() for item in definition.permissions),
    }
    operator_required = "approval:operator" in markers or any(
        marker in value for value in markers for marker in _HIGH_RISK_MARKERS
    )
    risk = "high" if operator_required or not definition.idempotent else "medium"
    return ApprovalPolicy(
        True,
        "human",
        "operator" if operator_required else "owner",
        risk,
        f"capability declares '{side_effect}' side effects",
    )


def approval_input_preview(inputs: dict[str, Any], data_classification: str) -> dict[str, Any]:
    """Produce a bounded preview without leaking confidential payload values."""
    if data_classification in {"confidential", "restricted"}:
        return {"fields": sorted(str(key) for key in inputs), "values": "[REDACTED]"}
    value = redact_runtime_value(inputs)
    return dict(value) if isinstance(value, dict) else {"value": value}
