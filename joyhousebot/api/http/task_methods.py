"""Helpers for task HTTP endpoint payloads."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from joyhousebot.services.tasks.task_service import (
    get_task_http,
    list_task_events_http,
    list_tasks_http,
)
from joyhousebot.services.errors import ServiceError


def list_tasks_response(*, store: Any, status: str | None, limit: int) -> dict[str, Any]:
    """Build list response payload for GET /tasks."""
    return list_tasks_http(store, status=status, limit=limit)


def get_task_response(*, store: Any, task_id: str) -> dict[str, Any]:
    """Build detail response payload for GET /tasks/{task_id}. Raises 404 if not found."""
    try:
        return get_task_http(store, task_id)
    except ServiceError as exc:
        if exc.code == "NOT_FOUND":
            raise HTTPException(status_code=404, detail=exc.message) from exc
        raise HTTPException(status_code=400, detail=exc.message) from exc


def list_task_events_response(*, store: Any, task_id: str, limit: int) -> dict[str, Any]:
    """Build events response payload for GET /tasks/{task_id}/events."""
    return list_task_events_http(store, task_id=task_id, limit=limit)
