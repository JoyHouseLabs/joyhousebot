"""PostgreSQL projections and admission rules for cumulative Schedule governance."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from joyhousebot.domain.schedules import CronJob
from joyhousebot.scheduling.row_mapper import schedule_job_from_row
from joyhousebot.storage.json_codec import Jsonb

_DB_NOW_MS = "(EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::bigint"


class ScheduleGovernanceRepository:
    """Own cumulative budgets, pause/resume audit, and read-only summaries."""

    def __init__(self, store: Any) -> None:
        self.store = store

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        with self.store._pool.connection() as connection:
            with connection.transaction():
                yield connection

    def resume(
        self,
        schedule_id: str,
        *,
        user_id: str,
        next_run_at_ms: int | None,
        now_ms: int,
        reset_counters: bool,
        actor_id: str,
    ) -> CronJob | None:
        """Explicitly close a Runtime pause and append an immutable audit event."""
        with self._connection() as connection:
            row = connection.execute(
                """UPDATE schedules SET paused=FALSE,pause_reason=NULL,paused_at_ms=NULL,
                       consecutive_failures=CASE WHEN %s THEN 0 ELSE consecutive_failures END,
                       consecutive_quiet=CASE WHEN %s THEN 0 ELSE consecutive_quiet END,
                       next_run_at_ms=CASE WHEN enabled THEN %s ELSE NULL END,
                       lease_owner=NULL,lease_until_ms=NULL,updated_at_ms=%s
                   WHERE schedule_id=%s AND user_id=%s AND paused RETURNING *""",
                (
                    reset_counters,
                    reset_counters,
                    next_run_at_ms,
                    now_ms,
                    schedule_id,
                    user_id,
                ),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM schedules WHERE schedule_id=%s AND user_id=%s",
                    (schedule_id, user_id),
                ).fetchone()
                return schedule_job_from_row(row) if row else None
            connection.execute(
                """INSERT INTO schedule_governance_events
                       (event_id,schedule_id,user_id,event_type,reason,details,created_at_ms)
                   VALUES (%s,%s,%s,'resumed',%s,%s,%s)""",
                (
                    uuid.uuid4().hex,
                    schedule_id,
                    user_id,
                    f"explicit resume by {actor_id}",
                    Jsonb({"reset_counters": reset_counters, "actor_id": actor_id}),
                    now_ms,
                ),
            )
        return schedule_job_from_row(row)

    def admit(self, job: CronJob, *, worker_id: str) -> dict[str, Any]:
        """Atomically enforce cumulative limits immediately before Run submission."""
        if job.state.claim_scope == "occurrence":
            return {"allowed": True}
        with self._connection() as connection:
            occurrence = connection.execute(
                """SELECT * FROM schedule_occurrences
                   WHERE occurrence_id=%s AND lease_owner=%s AND lease_version=%s
                   FOR UPDATE""",
                (job.state.occurrence_id, worker_id, job.lease_version),
            ).fetchone()
            schedule = connection.execute(
                "SELECT * FROM schedules WHERE schedule_id=%s FOR UPDATE",
                (job.id,),
            ).fetchone()
            if occurrence is None or schedule is None:
                return {"allowed": False, "lost_lease": True}
            if occurrence["admitted_at_ms"] is not None:
                return {"allowed": True}
            policy = _json(occurrence["policy"])
            now_ms = int(
                connection.execute(f"SELECT {_DB_NOW_MS} AS now_ms").fetchone()[
                    "now_ms"
                ]
            )
            usage = self._window_usage(
                connection,
                schedule_id=job.id,
                window_ms=policy.get("window_ms"),
                now_ms=now_ms,
            )
            reason = _governance_violation(schedule, policy, usage, now_ms)
            if reason is not None:
                self._pause_claimed(
                    connection,
                    job=job,
                    reason=reason,
                    usage=usage,
                    now_ms=now_ms,
                )
                return {"allowed": False, "reason": reason, **usage}
            connection.execute(
                "UPDATE schedule_occurrences SET admitted_at_ms=%s WHERE occurrence_id=%s",
                (now_ms, job.state.occurrence_id),
            )
            connection.execute(
                """UPDATE schedules SET admitted_occurrences=admitted_occurrences+1,
                       updated_at_ms=%s WHERE schedule_id=%s""",
                (now_ms, job.id),
            )
            return {"allowed": True, **usage}

    @staticmethod
    def _pause_claimed(
        connection: Any,
        *,
        job: CronJob,
        reason: str,
        usage: dict[str, Any],
        now_ms: int,
    ) -> None:
        connection.execute(
            """UPDATE schedules SET paused=TRUE,pause_reason=%s,paused_at_ms=%s,
                   next_run_at_ms=NULL,last_status='paused_governance',last_error=%s,
                   lease_owner=NULL,lease_until_ms=NULL,updated_at_ms=%s
               WHERE schedule_id=%s""",
            (reason, now_ms, reason, now_ms, job.id),
        )
        connection.execute(
            """UPDATE schedule_occurrences
               SET status='paused_governance',error=%s,outcome_kind='governance_pause',
                   finished_at_ms=%s,lease_owner=NULL,lease_until_ms=NULL
               WHERE occurrence_id=%s""",
            (reason, now_ms, job.state.occurrence_id),
        )
        connection.execute(
            """INSERT INTO schedule_governance_events
                   (event_id,schedule_id,user_id,occurrence_id,event_type,reason,
                    details,created_at_ms)
               VALUES (%s,%s,%s,%s,'paused',%s,%s,%s)""",
            (
                uuid.uuid4().hex,
                job.id,
                job.user_id,
                job.state.occurrence_id,
                reason,
                Jsonb(usage),
                now_ms,
            ),
        )

    def execution_summary(
        self, schedule_id: str, *, user_id: str
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            schedule = connection.execute(
                "SELECT * FROM schedules WHERE schedule_id=%s AND user_id=%s",
                (schedule_id, user_id),
            ).fetchone()
            if schedule is None:
                return None
            policy = _json(schedule["policy"])
            now_ms = int(
                connection.execute(f"SELECT {_DB_NOW_MS} AS now_ms").fetchone()[
                    "now_ms"
                ]
            )
            usage = self._window_usage(
                connection,
                schedule_id=schedule_id,
                window_ms=policy.get("window_ms"),
                now_ms=now_ms,
            )
            totals = connection.execute(
                """SELECT COUNT(*) AS occurrences,
                          COUNT(*) FILTER (WHERE outcome_kind='success') AS successes,
                          COUNT(*) FILTER (WHERE outcome_kind='failure') AS failures,
                          COUNT(*) FILTER (WHERE outcome_kind='quiet') AS quiet
                   FROM schedule_occurrences WHERE schedule_id=%s""",
                (schedule_id,),
            ).fetchone()
            events = connection.execute(
                """SELECT event_type,reason,details,created_at_ms
                   FROM schedule_governance_events WHERE schedule_id=%s
                   ORDER BY created_at_ms DESC,event_id DESC LIMIT 20""",
                (schedule_id,),
            ).fetchall()
        return _summary(schedule_id, schedule, totals, usage, events)

    @staticmethod
    def _window_usage(
        connection: Any,
        *,
        schedule_id: str,
        window_ms: int | None,
        now_ms: int,
    ) -> dict[str, Any]:
        if window_ms is None:
            return _empty_window()
        cutoff = now_ms - int(window_ms)
        row = connection.execute(
            """WITH linked AS (
                   SELECT DISTINCT link.run_id
                   FROM schedule_occurrence_runs link
                   JOIN schedule_occurrences occurrence
                     ON occurrence.occurrence_id=link.occurrence_id
                   WHERE occurrence.schedule_id=%s AND link.submitted_at_ms>=%s
               )
               SELECT (SELECT COUNT(*) FROM linked) AS runs,
                      COUNT(invocation.invocation_id) AS model_invocations,
                      COALESCE(SUM(invocation.cost_usd),0) AS cost_usd,
                      COUNT(invocation.invocation_id) FILTER (
                        WHERE COALESCE(invocation.usage->>'billing_status',CASE
                          WHEN invocation.cache_status='hit' THEN 'not_billed'
                          WHEN invocation.cost_usd<>0 THEN 'exact'
                          ELSE 'missing' END)='missing'
                      ) AS missing_billing_invocations
               FROM linked
               LEFT JOIN model_invocations invocation ON invocation.run_id=linked.run_id""",
            (schedule_id, cutoff),
        ).fetchone()
        invocations = int(row["model_invocations"] or 0)
        missing = int(row["missing_billing_invocations"] or 0)
        return {
            "window_started_at_ms": cutoff,
            "runs": int(row["runs"] or 0),
            "model_invocations": invocations,
            "cost_usd": float(row["cost_usd"] or 0),
            "missing_billing_invocations": missing,
            "billing_status": (
                "missing"
                if invocations and missing >= invocations
                else "partial"
                if missing
                else "exact"
            ),
        }


def _json(value: Any) -> dict[str, Any]:
    return dict(json.loads(value) if isinstance(value, str) else value or {})


def _empty_window() -> dict[str, Any]:
    return {
        "window_started_at_ms": None,
        "runs": 0,
        "model_invocations": 0,
        "cost_usd": 0.0,
        "missing_billing_invocations": 0,
        "billing_status": "exact",
    }


def _governance_violation(
    schedule: Any, policy: dict[str, Any], usage: dict[str, Any], now_ms: int
) -> str | None:
    if bool(schedule["paused"]):
        return str(schedule["pause_reason"] or "schedule is paused")
    ends_at_ms = policy.get("ends_at_ms")
    if ends_at_ms is not None and now_ms >= int(ends_at_ms):
        return "schedule lifecycle end time reached"
    maximum = policy.get("max_occurrences")
    if maximum is not None and int(schedule["admitted_occurrences"] or 0) >= int(
        maximum
    ):
        return "schedule occurrence budget exhausted"
    max_runs = policy.get("max_runs_per_window")
    if max_runs is not None and usage["runs"] >= int(max_runs):
        return "schedule Run window budget exhausted"
    max_cost = policy.get("max_cost_usd_per_window")
    if max_cost is not None and usage["missing_billing_invocations"]:
        return "schedule cost budget cannot be enforced because billing is incomplete"
    if max_cost is not None and usage["cost_usd"] >= float(max_cost):
        return "schedule cost window budget exhausted"
    return None


def _summary(
    schedule_id: str,
    schedule: Any,
    totals: Any,
    usage: dict[str, Any],
    events: list[Any],
) -> dict[str, Any]:
    return {
        "scheduleId": schedule_id,
        "enabled": bool(schedule["enabled"]),
        "paused": bool(schedule["paused"]),
        "pauseReason": schedule["pause_reason"],
        "pausedAtMs": schedule["paused_at_ms"],
        "admittedOccurrences": int(schedule["admitted_occurrences"] or 0),
        "consecutiveFailures": int(schedule["consecutive_failures"] or 0),
        "consecutiveQuiet": int(schedule["consecutive_quiet"] or 0),
        "occurrences": int(totals["occurrences"] or 0),
        "successes": int(totals["successes"] or 0),
        "failures": int(totals["failures"] or 0),
        "quiet": int(totals["quiet"] or 0),
        "window": usage,
        "events": [
            {
                "type": row["event_type"],
                "reason": row["reason"],
                "details": dict(row["details"] or {}),
                "createdAtMs": row["created_at_ms"],
            }
            for row in events
        ],
    }


__all__ = ["ScheduleGovernanceRepository"]
