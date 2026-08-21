"""Safe user-facing projection of durable loop decisions."""

from __future__ import annotations

from typing import Any


def loop_decision_public_dict(record: Any) -> dict[str, Any]:
    """Expose decision evidence without prompts, plans, or worker fencing data."""

    details = dict(record.details or {})
    return {
        "decision_id": record.decision_id,
        "run_id": record.run_id,
        "task_id": record.task_id,
        "scope": record.scope,
        "decision_index": record.decision_index,
        "attempt": record.attempt,
        "decision": record.decision,
        "reason_code": record.reason_code,
        "summary": record.summary,
        "input_hash": record.input_hash,
        "output_hash": record.output_hash,
        "max_replans": record.max_replans,
        "replans_used": details.get("replans_used"),
        "next_attempt": details.get("next_attempt"),
        "created_at": record.created_at,
    }
