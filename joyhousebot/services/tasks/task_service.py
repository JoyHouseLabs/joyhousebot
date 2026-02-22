"""Shared tasks domain operations for HTTP adapters."""

from __future__ import annotations

from typing import Any

from joyhousebot.services.errors import ServiceError


def _build_task_summary(task: Any) -> dict[str, Any]:
    """Build compact task payload for list endpoint."""
    return {
        "task_id": task.task_id,
        "source": task.source,
        "task_type": task.task_type,
        "task_version": task.task_version,
        "status": task.status,
        "retry_count": task.retry_count,
        "next_retry_at": task.next_retry_at,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def _build_task_detail(task: Any) -> dict[str, Any]:
    """Build detailed task payload for single task endpoint."""
    return {
        "task_id": task.task_id,
        "source": task.source,
        "task_type": task.task_type,
        "task_version": task.task_version,
        "payload": task.payload,
        "status": task.status,
        "retry_count": task.retry_count,
        "next_retry_at": task.next_retry_at,
        "error": str(task.error) if task.error else None,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def list_tasks_http(store: Any, *, status: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Build list response payload for GET /tasks."""
    tasks = store.list_tasks(limit=limit, status=status)
    return {"ok": True, "data": [_build_task_summary(t) for t in tasks]}


def get_task_http(store: Any, task_id: str) -> dict[str, Any]:
    """Build detail response payload for GET /tasks/{task_id}. Raises ServiceError NOT_FOUND if missing."""
    task = store.get_task(task_id)
    if not task:
        raise ServiceError(code="NOT_FOUND", message="Task not found")
    return {"ok": True, "data": _build_task_detail(task)}


def list_task_events_http(store: Any, *, task_id: str, limit: int = 100) -> dict[str, Any]:
    """Build events response payload for GET /tasks/{task_id}/events."""
    bounded = max(1, min(1000, limit))
    events = store.list_task_events(task_id=task_id, limit=bounded)
    return {"ok": True, "data": events}
