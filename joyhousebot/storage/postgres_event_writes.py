"""Shared PostgreSQL event writes used by transactional state transitions."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from joyhousebot.storage.json_codec import Jsonb


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def hydrate_event_identity(connection: Any, event: Any) -> Any:
    """Fill immutable Run identity while already holding the caller transaction."""
    if all((event.root_run_id, event.user_id, event.session_id, event.agent_id)):
        return event
    row = connection.execute(
        """SELECT root_run_id,parent_run_id,parent_task_id,user_id,session_id,agent_id
           FROM runtime_runs WHERE run_id=%s""",
        (event.run_id,),
    ).fetchone()
    if row is None:
        return event
    return replace(
        event,
        root_run_id=event.root_run_id or row["root_run_id"] or event.run_id,
        parent_run_id=event.parent_run_id or row["parent_run_id"],
        parent_task_id=event.parent_task_id or row["parent_task_id"],
        user_id=event.user_id or row["user_id"],
        session_id=event.session_id or row["session_id"],
        agent_id=event.agent_id or row["agent_id"],
    )


def insert_runtime_event(connection: Any, event: Any) -> Any:
    """Insert one idempotent event using the caller's open transaction."""
    event = hydrate_event_identity(connection, event)
    row = connection.execute(
        """INSERT INTO runtime_events
               (event_id,run_id,task_id,root_run_id,parent_run_id,parent_task_id,
                user_id,session_id,agent_id,turn_id,span_id,parent_span_id,tool_call_id,
                attempt,phase,status,visibility,summary,worker_id,lease_version,
                schema_version,event_type,data,created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamptz)
           ON CONFLICT(event_id) DO UPDATE SET event_id=EXCLUDED.event_id
           RETURNING sequence,created_at""",
        (
            event.event_id,
            event.run_id,
            event.task_id,
            event.root_run_id,
            event.parent_run_id,
            event.parent_task_id,
            event.user_id,
            event.session_id,
            event.agent_id,
            event.turn_id,
            event.span_id,
            event.parent_span_id,
            event.tool_call_id,
            event.attempt,
            event.phase,
            event.status,
            event.visibility,
            event.summary,
            event.worker_id,
            event.lease_version,
            event.schema_version,
            event.type,
            Jsonb(event.data),
            event.created_at,
        ),
    ).fetchone()
    assert row is not None
    return replace(
        event,
        sequence=int(row["sequence"]),
        created_at=_iso(row["created_at"]) or event.created_at,
    )


def project_runtime_event(connection: Any, event: Any) -> None:
    """Update the Run's observable projection in the event transaction."""
    if event.sequence is None:
        raise ValueError("persisted event sequence is required")
    span_delta = (
        1
        if event.type in {"model.request.started", "capability.started"}
        else (
            -1
            if event.type
            in {
                "model.response.completed",
                "capability.completed",
                "capability.failed",
            }
            else 0
        )
    )
    connection.execute(
        """UPDATE runtime_runs SET
               root_run_id=COALESCE(root_run_id,%s),
               current_phase=COALESCE(%s,current_phase),
               status_summary=COALESCE(%s,status_summary),
               status_reason=COALESCE(%s,status_reason),
               next_action=COALESCE(%s,next_action),
               waiting_on=COALESCE(%s,waiting_on),
               active_turn_id=COALESCE(%s,active_turn_id),
               active_span_count=GREATEST(0,active_span_count + %s),
               completed_task_count=(SELECT count(*) FROM runtime_tasks
                   WHERE run_id=%s AND status IN
                     ('completed','failed','cancelled','timed_out','skipped')),
               last_event_sequence=GREATEST(last_event_sequence,%s),
               last_progress_at=clock_timestamp(),updated_at=clock_timestamp()
           WHERE run_id=%s""",
        (
            event.root_run_id or event.run_id,
            event.phase,
            event.summary,
            event.data.get("reason"),
            event.data.get("next_action"),
            event.data.get("waiting_on"),
            event.turn_id,
            span_delta,
            event.run_id,
            event.sequence,
            event.run_id,
        ),
    )


def append_runtime_event_in_transaction(connection: Any, event: Any) -> Any:
    persisted = insert_runtime_event(connection, event)
    project_runtime_event(connection, persisted)
    return persisted
