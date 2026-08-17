"""Storage contract for the durable, distributed agent runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

DESTRUCTIVE_MIGRATE_PHRASE = "DROP_ALL_TABLES"


def destructive_migrate_enabled() -> bool:
    """Whether a development schema reset is explicitly allowed.

    Development-only escape hatch: the environment variable must equal the
    exact phrase ``DROP_ALL_TABLES``; truthy values such as ``1`` no longer
    qualify, so a casually exported flag cannot wipe production data.
    """
    return (
        os.environ.get("PORTHOUSE_DESTRUCTIVE_MIGRATE", "").strip() == DESTRUCTIVE_MIGRATE_PHRASE
    )


@dataclass(slots=True)
class RuntimeRunRecord:
    run_id: str
    user_id: str
    session_id: str
    agent_id: str
    kind: str
    status: str
    prompt: str
    options: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    idempotency_key: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str
    lease_owner: str | None
    lease_expires_at: str | None
    lease_version: int = 0
    root_run_id: str | None = None
    parent_run_id: str | None = None
    parent_task_id: str | None = None
    current_phase: str | None = None
    status_summary: str | None = None
    status_reason: str | None = None
    next_action: str | None = None
    waiting_on: str | None = None
    active_turn_id: str | None = None
    active_span_count: int = 0
    completed_task_count: int = 0
    total_task_count: int = 0
    last_event_sequence: int = 0
    last_progress_at: str | None = None
    cancel_requested_at: str | None = None
    cancel_reason: str | None = None
    graph_revision_id: str | None = None


@dataclass(slots=True)
class RuntimeTaskRecord:
    task_id: str
    run_id: str
    agent_id: str
    parent_task_id: str | None
    name: str
    status: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    priority: int
    attempt: int
    max_attempts: int
    available_at: str
    lease_owner: str | None
    lease_expires_at: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str
    lease_version: int = 0


@dataclass(slots=True)
class RuntimeLogRecord:
    sequence: int
    run_id: str
    task_id: str | None
    worker_id: str | None
    level: str
    stage: str
    message: str
    data: dict[str, Any]
    created_at: str


@dataclass(slots=True)
class RequestTraceEventRecord:
    """One immutable milestone in an end-to-end request timeline."""

    sequence: int
    event_id: str
    tracker_id: str
    request_id: str
    parent_request_id: str | None
    user_id: str | None
    run_id: str | None
    transport: str
    direction: str
    operation: str
    stage: str
    status: str | None
    data: dict[str, Any]
    created_at: str
