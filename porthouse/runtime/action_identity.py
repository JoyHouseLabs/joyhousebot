"""Deterministic identities for durable Agent turns and actions."""

from __future__ import annotations

import hashlib
from typing import Any

from porthouse.domain.capabilities import CapabilityRef
from porthouse.domain.identity import canonical_json, payload_hash

__all__ = ["canonical_json", "durable_action_id", "durable_turn_id", "payload_hash"]


def durable_turn_id(
    run_id: str,
    task_id: str | None,
    turn_index: int,
    *,
    scope: str = "execution",
) -> str:
    material = canonical_json(
        {
            "run_id": run_id,
            "task_id": task_id or "",
            "scope": scope,
            "turn_index": int(turn_index),
        }
    )
    return f"turn_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"


def durable_action_id(
    *,
    run_id: str,
    task_id: str | None,
    turn_index: int,
    action_index: int,
    capability_ref: CapabilityRef,
    inputs: dict[str, Any],
) -> str:
    """Build the V2 action identity from immutable execution inputs."""

    material = canonical_json(
        {
            "run_id": run_id,
            "task_id": task_id or "",
            "turn_index": int(turn_index),
            "action_index": int(action_index),
            "capability_ref": capability_ref.to_dict(),
            "input": inputs,
        }
    )
    return f"act_{hashlib.sha256(material.encode('utf-8')).hexdigest()}"
