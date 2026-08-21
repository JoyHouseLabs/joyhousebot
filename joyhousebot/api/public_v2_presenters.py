"""Stable, privacy-bounded projections for the public execution API."""

from __future__ import annotations

from typing import Any

from joyhousebot.application.presenters import record_dict
from joyhousebot.runtime.models import AgentEvent, EventVisibility
from joyhousebot.runtime.narrative import redact_runtime_value

_RUN_STATUS = {
    "queued": "queued",
    "scheduled": "queued",
    "planning": "running",
    "running": "running",
    "waiting_input": "waiting_for_input",
    "waiting_approval": "waiting_for_approval",
    "waiting_external": "running",
    "paused": "running",
    "completed": "succeeded",
    "failed": "failed",
    "timed_out": "failed",
    "cancelled": "cancelled",
}


def public_run(value: Any) -> dict[str, Any]:
    record = record_dict(value)
    status = _RUN_STATUS.get(str(record.get("status")), "running")
    return {
        "id": str(record["run_id"]),
        "status": status,
        "progress": {
            "phase": record.get("current_phase"),
            "summary": str(record.get("status_summary") or ""),
            "completed": int(record.get("completed_task_count") or 0),
            "total": int(record.get("total_task_count") or 0),
        },
        "pending_action": record.get("next_action"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
    }


def public_artifact(value: dict[str, Any]) -> dict[str, Any]:
    """Expose artifact content and integrity, never its private storage address."""
    return {
        "id": str(value["artifact_id"]),
        "run_id": str(value["run_id"]),
        "name": str(value["name"]),
        "type": str(value["artifact_type"]),
        "media_type": str(value["media_type"]),
        "schema_version": int(value.get("schema_version") or 1),
        "content": value.get("content"),
        "content_sha256": str(value.get("content_sha256") or ""),
        "created_at": value.get("created_at"),
    }


def public_approval(value: Any) -> dict[str, Any]:
    record = record_dict(value)
    status = str(record["status"])
    allowed_decisions = (
        ["approve", "reject", "request_changes"]
        if status == "pending"
        else ["revoke"]
        if status == "approved"
        else []
    )
    capability = dict(record.get("capability_ref") or {})
    input_preview = redact_runtime_value(record.get("input_preview") or {})
    assert isinstance(input_preview, dict)
    return {
        "id": str(record["approval_id"]),
        "run_id": str(record["run_id"]),
        "status": status,
        "summary": str(capability.get("name") or capability.get("capability_id") or "Action"),
        "risk": str(record.get("risk") or "unknown"),
        "data_classification": str(record.get("data_classification") or "internal"),
        "input_preview": input_preview,
        "allowed_decisions": allowed_decisions,
        "requested_at": record.get("requested_at"),
        "expires_at": record.get("expires_at"),
        "resolved_at": record.get("resolved_at"),
    }


def public_input_request(value: Any) -> dict[str, Any]:
    record = record_dict(value)
    return {
        "id": str(record["input_request_id"]),
        "run_id": str(record["run_id"]),
        "question": str(record["question"]),
        "fields": list(record.get("fields") or []),
        "presentation": dict(record.get("presentation") or {}),
        "expires_at": record.get("expires_at"),
        "created_at": record.get("created_at"),
    }


def public_operation_progress(value: Any, latest_event: Any | None = None) -> dict[str, Any]:
    """Expose only user-facing progress, never provider operation identity."""
    record = record_dict(value)
    event = record_dict(latest_event) if latest_event is not None else {}
    payload = dict(event.get("payload") or {})
    status = {
        "succeeded": "succeeded",
        "failed": "failed",
        "manual_required": "needs_attention",
    }.get(str(record.get("status")), "running")

    def item(label_key: str, position_key: str) -> dict[str, Any] | None:
        label = str(payload.get(label_key) or "").strip()[:500]
        if not label:
            return None
        position = payload.get(position_key)
        return {
            "position": int(position) if isinstance(position, int) and position >= 0 else None,
            "label": label,
        }

    completed = payload.get("completed")
    total = payload.get("total")
    return {
        "id": str(record["reconciliation_id"]),
        "status": status,
        "summary": str(
            event.get("summary") or record.get("progress_summary") or "外部操作正在执行"
        )[:1000],
        "percent": record.get("progress_percent"),
        "completed": completed if isinstance(completed, int) and completed >= 0 else None,
        "total": total if isinstance(total, int) and total >= 0 else None,
        "current_item": item("current_label", "current_position"),
        "next_item": item("next_label", "next_position"),
        "updated_at": record.get("updated_at"),
    }


def public_event(event: AgentEvent) -> dict[str, Any] | None:
    """Collapse internal execution detail into the small public event vocabulary."""
    if event.visibility != EventVisibility.PUBLIC.value:
        return None
    projected = _project_event(event)
    if projected is None:
        return None
    event_name, data = projected
    return {
        "sequence": int(event.sequence or 0),
        "event": event_name,
        "run_id": event.run_id,
        "timestamp": event.created_at,
        "data": data,
    }


def _project_event(event: AgentEvent) -> tuple[str, dict[str, Any]] | None:
    data = event.data
    if event.type == "message.delta":
        return "run.output.delta", {"content": str(data.get("content") or "")}
    if event.type == "message.completed":
        return "run.output.completed", {"content": str(data.get("content") or "")}
    if event.type == "artifact.created":
        return "artifact.created", _select(data, "artifact_id", "name", "media_type")
    if event.type in {"approval.requested", "approval.resolved"}:
        return event.type, _select(data, "approval_id", "status", "resolution")
    if event.type == "user_input.requested":
        return "input.requested", _select(
            data, "input_request_id", "question", "fields", "presentation"
        )
    if event.type == "user_input.resolved":
        return "input.resolved", _select(data, "input_request_id", "fields")
    if event.type.startswith("phase."):
        return "run.progress", {
            "phase": event.phase,
            "status": event.status,
            "summary": event.summary,
        }
    if event.type == "run.history_purged":
        return "run.history_gap", {"summary": event.summary}
    if event.type.startswith("run."):
        public_status = _event_run_status(event)
        name = (
            f"run.{public_status}"
            if public_status in {"succeeded", "failed", "cancelled"}
            else "run.status_changed"
        )
        return name, {
            "status": public_status,
            "phase": event.phase,
            "summary": event.summary,
        }
    return None


def _event_run_status(event: AgentEvent) -> str:
    status_by_type = {
        "run.accepted": "queued",
        "run.queued": "queued",
        "run.scheduled": "queued",
        "run.claimed": "running",
        "run.started": "running",
        "run.resumed": "running",
        "run.completed": "succeeded",
        "run.failed": "failed",
        "run.timed_out": "failed",
        "run.cancelled": "cancelled",
        "run.waiting_approval": "waiting_for_approval",
    }
    return status_by_type.get(event.type) or _RUN_STATUS.get(str(event.status), "running")


def _select(value: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value[key] for key in keys if key in value}


__all__ = [
    "public_approval",
    "public_artifact",
    "public_event",
    "public_input_request",
    "public_operation_progress",
    "public_run",
]
