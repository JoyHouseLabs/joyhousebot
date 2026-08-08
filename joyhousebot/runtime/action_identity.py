"""Deterministic identities for durable Agent turns and actions."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from joyhousebot.domain.capabilities import CapabilityRef


def canonical_json(value: Any) -> str:
    """Return the stable JSON representation used by execution identities."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


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
