"""Transactional Schedule projections driven by terminal Runtime Runs."""

from __future__ import annotations

import json
import uuid
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
            quiet=False,
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
        quiet=quiet,
    )
    if bool(occurrence["delete_after_run"]):
        connection.execute(
            "DELETE FROM schedules WHERE schedule_id=%s", (occurrence["schedule_id"],)
        )


def _update_latest_schedule_state(
    connection: Any,
    occurrence: Any,
    *,
    status: str,
    error: str | None,
    quiet: bool,
) -> None:
    if status != "retry_wait":
        outcome_kind = "quiet" if quiet else "success" if status == "completed" else "failure"
        _project_schedule_governance(
            connection,
            occurrence,
            outcome_kind=outcome_kind,
        )
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


def project_schedule_non_run_terminal(
    connection: Any,
    *,
    occurrence_id: str,
    status: str,
    error: str | None,
) -> None:
    """Project terminal scheduler outcomes that never created a Runtime Run."""
    occurrence = connection.execute(
        "SELECT * FROM schedule_occurrences WHERE occurrence_id=%s",
        (occurrence_id,),
    ).fetchone()
    if occurrence is None:
        return
    outcome_kind = (
        "quiet"
        if status == "skipped_unchanged"
        else "failure"
        if status in {"error", "skipped_app_unavailable"}
        else "neutral"
    )
    _project_schedule_governance(
        connection,
        occurrence,
        outcome_kind=outcome_kind,
    )


def _project_schedule_governance(
    connection: Any, occurrence: Any, *, outcome_kind: str
) -> None:
    applied = connection.execute(
        """UPDATE schedule_occurrences SET outcome_kind=%s
           WHERE occurrence_id=%s AND outcome_kind IS NULL
           RETURNING occurrence_id""",
        (outcome_kind, occurrence["occurrence_id"]),
    ).fetchone()
    if applied is None or outcome_kind == "neutral":
        return
    schedule = connection.execute(
        "SELECT * FROM schedules WHERE schedule_id=%s FOR UPDATE",
        (occurrence["schedule_id"],),
    ).fetchone()
    if schedule is None:
        return
    policy = _json(schedule["policy"])
    failures = int(schedule["consecutive_failures"] or 0)
    quiet = int(schedule["consecutive_quiet"] or 0)
    if outcome_kind == "failure":
        failures, quiet = failures + 1, 0
    elif outcome_kind == "quiet":
        failures, quiet = 0, quiet + 1
    else:
        failures, quiet = 0, 0

    pause_reason = _circuit_reason(
        connection,
        occurrence=occurrence,
        schedule=schedule,
        policy=policy,
        failures=failures,
        quiet=quiet,
    )
    idle_delay = _idle_backoff_ms(occurrence, policy, quiet)
    connection.execute(
        f"""UPDATE schedules SET consecutive_failures=%s,consecutive_quiet=%s,
               paused=CASE WHEN %s::text IS NULL THEN paused ELSE TRUE END,
               pause_reason=COALESCE(%s::text,pause_reason),
               paused_at_ms=CASE WHEN %s::text IS NULL THEN paused_at_ms ELSE {_DB_NOW_MS} END,
               next_run_at_ms=CASE
                   WHEN %s::text IS NOT NULL THEN NULL
                   WHEN %s::bigint IS NOT NULL AND next_run_at_ms IS NOT NULL
                     THEN GREATEST(next_run_at_ms,{_DB_NOW_MS}+%s::bigint)
                   ELSE next_run_at_ms END,
               updated_at_ms={_DB_NOW_MS}
           WHERE schedule_id=%s""",
        (
            failures,
            quiet,
            pause_reason,
            pause_reason,
            pause_reason,
            pause_reason,
            idle_delay,
            idle_delay,
            occurrence["schedule_id"],
        ),
    )
    if pause_reason is not None and not bool(schedule["paused"]):
        connection.execute(
            f"""INSERT INTO schedule_governance_events
                   (event_id,schedule_id,user_id,occurrence_id,event_type,reason,
                    details,created_at_ms)
               VALUES (%s,%s,%s,%s,'paused',%s,%s,{_DB_NOW_MS})""",
            (
                uuid.uuid4().hex,
                occurrence["schedule_id"],
                occurrence["user_id"],
                occurrence["occurrence_id"],
                pause_reason,
                Jsonb(
                    {
                        "outcome_kind": outcome_kind,
                        "consecutive_failures": failures,
                        "consecutive_quiet": quiet,
                    }
                ),
            ),
        )


def _circuit_reason(
    connection: Any,
    *,
    occurrence: Any,
    schedule: Any,
    policy: dict[str, Any],
    failures: int,
    quiet: int,
) -> str | None:
    maximum_failures = policy.get("max_consecutive_failures")
    if maximum_failures is not None and failures >= int(maximum_failures):
        return "schedule consecutive failure circuit opened"
    maximum_quiet = policy.get("max_consecutive_quiet")
    if maximum_quiet is not None and quiet >= int(maximum_quiet):
        return "schedule consecutive quiet circuit opened"
    maximum_occurrences = policy.get("max_occurrences")
    if maximum_occurrences is not None and int(
        schedule["admitted_occurrences"] or 0
    ) >= int(maximum_occurrences):
        return "schedule occurrence budget exhausted"
    window_ms = policy.get("window_ms")
    if window_ms is None:
        return None
    usage = _window_usage(
        connection,
        schedule_id=str(occurrence["schedule_id"]),
        window_ms=int(window_ms),
    )
    maximum_runs = policy.get("max_runs_per_window")
    if maximum_runs is not None and usage["runs"] >= int(maximum_runs):
        return "schedule Run window budget exhausted"
    maximum_cost = policy.get("max_cost_usd_per_window")
    if maximum_cost is not None and usage["missing_billing_invocations"]:
        return "schedule cost budget cannot be enforced because billing is incomplete"
    if maximum_cost is not None and usage["cost_usd"] >= float(maximum_cost):
        return "schedule cost window budget exhausted"
    return None


def _window_usage(
    connection: Any, *, schedule_id: str, window_ms: int
) -> dict[str, Any]:
    row = connection.execute(
        f"""WITH linked AS (
               SELECT DISTINCT link.run_id
               FROM schedule_occurrence_runs link
               JOIN schedule_occurrences occurrence
                 ON occurrence.occurrence_id=link.occurrence_id
               WHERE occurrence.schedule_id=%s
                 AND link.submitted_at_ms>={_DB_NOW_MS}-%s
           )
           SELECT (SELECT COUNT(*) FROM linked) AS runs,
                  COALESCE(SUM(invocation.cost_usd),0) AS cost_usd,
                  COUNT(invocation.invocation_id) FILTER (
                    WHERE COALESCE(invocation.usage->>'billing_status',CASE
                      WHEN invocation.cache_status='hit' THEN 'not_billed'
                      WHEN invocation.cost_usd<>0 THEN 'exact'
                      ELSE 'missing' END)='missing'
                  ) AS missing_billing_invocations
           FROM linked
           LEFT JOIN model_invocations invocation ON invocation.run_id=linked.run_id""",
        (schedule_id, window_ms),
    ).fetchone()
    return {
        "runs": int(row["runs"] or 0),
        "cost_usd": float(row["cost_usd"] or 0),
        "missing_billing_invocations": int(row["missing_billing_invocations"] or 0),
    }


def _idle_backoff_ms(
    occurrence: Any, policy: dict[str, Any], consecutive_quiet: int
) -> int | None:
    maximum = policy.get("idle_backoff_max_ms")
    multiplier = float(policy.get("idle_backoff_multiplier") or 1)
    if consecutive_quiet <= 0 or maximum is None or multiplier <= 1:
        return None
    schedule = _json(occurrence["schedule"])
    base = max(60_000, int(schedule.get("every_ms") or 60_000))
    exponent = min(20, max(0, consecutive_quiet - 1))
    return min(int(maximum), int(base * (multiplier**exponent)))


__all__ = [
    "enqueue_schedule_delivery",
    "project_schedule_non_run_terminal",
    "project_schedule_run_terminal",
]
