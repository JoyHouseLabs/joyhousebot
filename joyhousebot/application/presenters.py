"""Canonical JSON projections shared by HTTP and background consumers."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any


def record_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"unsupported record type: {type(value).__name__}")


def runtime_run_list_item(value: Any) -> dict[str, Any]:
    """Return the intentionally small projection used by paginated Run lists.

    A Run's plan, artifacts and final result can be very large.  Those belong
    to the single-Run diagnostics endpoint, never to a monitoring table.
    """
    record = record_dict(value)

    def _short(item: Any, maximum: int = 500) -> str:
        text = str(item or "")
        return text if len(text) <= maximum else f"{text[:maximum - 1]}…"

    return {
        key: record.get(key)
        for key in (
            "run_id", "user_id", "session_id", "agent_id", "kind", "status",
            "current_phase", "next_action", "completed_task_count", "total_task_count",
            "created_at", "updated_at", "started_at", "finished_at", "parent_run_id",
            "root_run_id",
        )
    } | {
        "prompt": _short(record.get("prompt")),
        "status_summary": _short(record.get("status_summary"), 280),
    }


def input_asset_public_dict(value: Any) -> dict[str, Any]:
    """Expose immutable input metadata without leaking storage credentials or paths."""
    record = record_dict(value)
    return {
        key: record.get(key)
        for key in (
            "asset_id",
            "original_name",
            "media_type",
            "content_sha256",
            "byte_size",
            "object_version",
            "status",
            "created_at",
            "deleted_at",
        )
    }


def public_capability_definition(value: dict[str, Any]) -> dict[str, Any]:
    """Project catalog metadata without adapter-private configuration.

    Configuration *schema* is safe metadata and lets operators render a
    validated settings editor. Actual values stay behind the dedicated admin
    runtime-settings endpoint.
    """

    result = dict(value)
    result.pop("configuration", None)
    return result
