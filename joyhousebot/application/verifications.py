"""Safe user-facing projection of durable verification evidence."""

from __future__ import annotations

from typing import Any


def verification_public_dict(record: Any) -> dict[str, Any]:
    """Exclude verifier policy bodies, worker identity, and lease fencing details."""

    return {
        "verification_id": record.verification_id,
        "run_id": record.run_id,
        "task_id": record.task_id,
        "turn_id": record.turn_id,
        "attempt": record.attempt,
        "verifier_id": record.verifier_id,
        "verifier_type": record.verifier_type,
        "verifier_version": record.verifier_version,
        "required": record.required,
        "repairable": record.repairable,
        "status": record.status,
        "input_hash": record.input_hash,
        "evidence": dict(record.evidence),
        "error": dict(record.error or {}) or None,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
    }
