"""Redacted user-facing projections for model context manifests."""

from __future__ import annotations

from typing import Any


def context_manifest_public_dict(record: Any) -> dict[str, Any]:
    """Expose provenance and budget evidence without source content or owner keys."""

    return {
        "manifest_id": record.manifest_id,
        "turn_id": record.turn_id,
        "run_id": record.run_id,
        "task_id": record.task_id,
        "scope": record.scope,
        "turn_index": record.turn_index,
        "request_hash": record.request_hash,
        "manifest_hash": record.manifest_hash,
        "budget_tokens": record.budget_tokens,
        "budget_strategy": record.budget_strategy,
        "estimated_tokens": record.estimated_tokens,
        "included_tokens": record.included_tokens,
        "excluded_tokens": record.excluded_tokens,
        "entry_count": len(record.entries),
        "created_at": record.created_at,
        "entries": [
            {
                "entry_id": item.entry_id,
                "ordinal": item.ordinal,
                "source_kind": item.source_kind,
                "source_id": item.source_id,
                "classification": item.classification,
                "authority": item.authority,
                "freshness": item.freshness,
                "content_hash": item.content_hash,
                "estimated_tokens": item.estimated_tokens,
                "priority": item.priority,
                "included": item.included,
                "included_reason": item.included_reason,
                "excluded_reason": item.excluded_reason,
                "citation_id": item.citation_id,
                "redaction_policy": item.redaction_policy,
                "compression": (item.metadata or {}).get("compression"),
            }
            for item in record.entries
        ],
    }
