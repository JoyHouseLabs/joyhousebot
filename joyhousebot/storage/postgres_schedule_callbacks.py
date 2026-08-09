"""Transactional Schedule projections driven by terminal Runtime Runs."""

from __future__ import annotations

import json
from typing import Any

from joyhousebot.storage.json_codec import Jsonb

_DB_NOW_MS = "(EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::bigint"


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return dict(json.loads(value) or {})
    return dict(value or {})


def _error_message(status: str, result: dict[str, Any], error: dict[str, Any]) -> str:
    return str(error.get("message") or result.get("error") or status)


def enqueue_schedule_delivery(
    connection: Any,
    *,
    occurrence_id: str,
    schedule_id: str,
    user_id: str,
    payload: dict[str, Any],
    content: str,
    run_id: str | None,
    attempt: int,
) -> tuple[str, str | None, str | None]:
    """Insert one deterministic outbox entry using the caller's transaction."""
    if not bool(payload.get("deliver")):
        return "not_requested", None, None
    if not payload.get("channel") or not payload.get("to"):
        return "dead", None, "delivery channel or target is missing"
    outbox = connection.execute(
        "SELECT to_regclass('channel_outbox') AS table_name"
    ).fetchone()
    if not outbox or not outbox["table_name"]:
        return "dead", None, "channel outbox is unavailable"

    outbound_id = f"schedule-delivery:{occurrence_id}"
    metadata = {
        "user_id": user_id,
        "schedule_id": schedule_id,
        "schedule_occurrence_id": occurrence_id,
        "run_id": run_id,
        "schedule_attempt": attempt,
    }
    connection.execute(
        f"""INSERT INTO channel_outbox
            (outbound_id,user_id,channel,chat_id,content,media,metadata,status,
             attempt,available_at_ms,lease_version,created_at_ms,updated_at_ms)
            VALUES (%s,%s,%s,%s,%s,'[]'::jsonb,%s,'pending',0,
                    {_DB_NOW_MS},0,{_DB_NOW_MS},{_DB_NOW_MS})
            ON CONFLICT(outbound_id) DO NOTHING""",
        (
            outbound_id,
            user_id,
            str(payload["channel"]),
            str(payload["to"]),
            content,
            Jsonb(metadata),
        ),
    )
    return "pending", outbound_id, None


def project_schedule_run_terminal(
    connection: Any,
    *,
    run_id: str,
    status: str,
    result: dict[str, Any] | None,
    error: dict[str, Any] | None,
) -> None:
    """Write Run outcome, retry decision, and optional delivery atomically.

    The function is a no-op when schedule tables are not installed. That keeps
    the Runtime usable in narrow tests while production workers always install
    the scheduling schema before executing Runs.
    """
    table = connection.execute(
        "SELECT to_regclass('schedule_occurrences') AS table_name"
    ).fetchone()
    if not table or not table["table_name"]:
        return
    occurrence = connection.execute(
        """SELECT * FROM schedule_occurrences
           WHERE run_id=%s AND status='submitted' FOR UPDATE""",
        (run_id,),
    ).fetchone()
    if occurrence is None:
        return

    policy = _json(occurrence["policy"])
    payload = _json(occurrence["payload"])
    result_data = dict(result or {})
    error_data = dict(error or {})
    attempt = max(1, int(occurrence["attempt"] or 1))
    max_run_retries = max(0, min(10, int(policy.get("max_run_retries") or 0)))
    retryable = status in {"failed", "timed_out"} and attempt <= max_run_retries
    error_message = _error_message(status, result_data, error_data)

    if retryable:
        base_backoff = max(1_000, int(policy.get("retry_backoff_ms") or 60_000))
        backoff = min(3_600_000, base_backoff * (2 ** max(0, attempt - 1)))
        connection.execute(
            f"""UPDATE schedule_occurrences SET status='retry_wait',error=%s,
                next_attempt_at_ms={_DB_NOW_MS}+%s,finished_at_ms=NULL,
                lease_owner=NULL,lease_until_ms=NULL
                WHERE occurrence_id=%s""",
            (error_message, backoff, occurrence["occurrence_id"]),
        )
        _update_latest_schedule_state(
            connection,
            occurrence,
            status="retry_wait",
            error=error_message,
        )
        return

    if status == "completed":
        content = str(result_data.get("content") or "定时任务已完成。")
    else:
        content = f"定时任务执行失败（{status}）：{error_message}"
    quiet_token = str(payload.get("quiet_token") or "NO_ACTION").strip()
    quiet = (
        status == "completed"
        and payload.get("kind") == "agent_monitor"
        and content.strip() == quiet_token
    )
    if quiet:
        delivery_status, delivery_outbound_id, delivery_error = (
            "suppressed",
            None,
            None,
        )
    else:
        delivery_status, delivery_outbound_id, delivery_error = enqueue_schedule_delivery(
            connection,
            occurrence_id=str(occurrence["occurrence_id"]),
            schedule_id=str(occurrence["schedule_id"]),
            user_id=str(occurrence["user_id"]),
            payload=payload,
            content=content,
            run_id=run_id,
            attempt=attempt,
        )

    connection.execute(
        f"""UPDATE schedule_occurrences SET status=%s,error=%s,
            next_attempt_at_ms=NULL,finished_at_ms={_DB_NOW_MS},
            delivery_status=%s,delivery_outbound_id=%s,delivery_error=%s,
            lease_owner=NULL,lease_until_ms=NULL
            WHERE occurrence_id=%s""",
        (
            status,
            None if status == "completed" else error_message,
            delivery_status,
            delivery_outbound_id,
            delivery_error,
            occurrence["occurrence_id"],
        ),
    )
    _update_latest_schedule_state(
        connection,
        occurrence,
        status=status,
        error=None if status == "completed" else error_message,
    )
    if bool(occurrence["delete_after_run"]):
        connection.execute(
            "DELETE FROM schedules WHERE schedule_id=%s", (occurrence["schedule_id"],)
        )


def _update_latest_schedule_state(
    connection: Any, occurrence: Any, *, status: str, error: str | None
) -> None:
    connection.execute(
        f"""UPDATE schedules SET last_status=%s,last_error=%s,
            last_run_at_ms={_DB_NOW_MS},updated_at_ms={_DB_NOW_MS}
            WHERE schedule_id=%s AND NOT EXISTS (
                SELECT 1 FROM schedule_occurrences newer
                WHERE newer.schedule_id=schedules.schedule_id
                  AND newer.scheduled_for_ms>%s
            )""",
        (status, error, occurrence["schedule_id"], occurrence["scheduled_for_ms"]),
    )
