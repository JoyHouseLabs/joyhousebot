"""PostgreSQL repository for schedules and occurrences."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from porthouse.domain.schedules import CronJob
from porthouse.scheduling.migrations import ensure_schedule_schema
from porthouse.scheduling.row_mapper import (
    occurrence_job_from_row,
    schedule_job_from_row,
)
from porthouse.storage.json_codec import Jsonb
from porthouse.storage.postgres_schedule_callbacks import (
    enqueue_schedule_delivery,
    project_schedule_run_terminal,
)

# Database time owns leases: every lease/due comparison uses the database
# clock so scheduler replicas never compare leases against skewed client
# wall clocks.  Time columns are bigint epoch milliseconds.
_DB_NOW_MS = "(EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::bigint"


class ScheduleRepository:
    """Own schedule rows; PostgreSQL uses row locks and SKIP LOCKED."""

    def __init__(self, store: Any) -> None:
        self.store = store
        if getattr(store, "backend_name", None) != "postgres":
            raise TypeError("ScheduleRepository requires PostgreSQL runtime store")
        self.migrate()

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        with self.store._pool.connection() as connection:
            with connection.transaction():
                yield connection

    def migrate(self) -> None:
        ensure_schedule_schema(self.store)

    def create(self, job: CronJob) -> CronJob:
        schedule = vars(job.schedule)
        payload = vars(job.payload)
        policy = vars(job.policy)
        columns = """schedule_id,user_id,name,agent_id,installation_id,enabled,schedule,
            payload,policy,next_run_at_ms,last_run_at_ms,last_status,last_error,
            delete_after_run,lease_owner,lease_until_ms,lease_version,created_at_ms,
            updated_at_ms"""
        values = (
                job.id,
                job.user_id,
                job.name,
                job.agent_id,
                job.installation_id,
                job.enabled,
                Jsonb(schedule),
                Jsonb(payload),
                Jsonb(policy),
                job.state.next_run_at_ms,
                job.state.last_run_at_ms,
                job.state.last_status,
                job.state.last_error,
                job.delete_after_run,
                None,
                None,
                0,
                job.created_at_ms,
                job.updated_at_ms,
            )
        with self._connection() as connection:
            row = connection.execute(
                f"INSERT INTO schedules ({columns}) VALUES ({','.join(['%s'] * 19)}) "
                "ON CONFLICT(schedule_id) DO NOTHING RETURNING *",
                values,
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM schedules WHERE schedule_id=%s AND user_id=%s",
                    (job.id, job.user_id),
                ).fetchone()
        if row is None:
            raise PermissionError("schedule id belongs to another user")
        return schedule_job_from_row(row)

    def list(self, *, user_id: str | None, include_disabled: bool) -> list[CronJob]:
        clauses: list[str] = []
        params: list[Any] = []
        placeholder = "%s"
        if user_id is not None:
            clauses.append(f"user_id={placeholder}")
            params.append(user_id)
        if not include_disabled:
            clauses.append("enabled=TRUE")
        query = "SELECT * FROM schedules"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY next_run_at_ms, created_at_ms"
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [schedule_job_from_row(row) for row in rows]

    def set_enabled(
        self,
        schedule_id: str,
        enabled: bool,
        *,
        user_id: str | None,
        next_run_at_ms: int | None,
        now_ms: int,
    ) -> CronJob | None:
        placeholder = "%s"
        params: list[Any] = [
            enabled,
            next_run_at_ms,
            now_ms,
            schedule_id,
        ]
        condition = f"schedule_id={placeholder}"
        if user_id is not None:
            condition += f" AND user_id={placeholder}"
            params.append(user_id)
        query = f"UPDATE schedules SET enabled={placeholder},next_run_at_ms={placeholder},updated_at_ms={placeholder},lease_owner=NULL,lease_until_ms=NULL WHERE {condition}"
        with self._connection() as connection:
            row = connection.execute(query + " RETURNING *", params).fetchone()
        return schedule_job_from_row(row) if row else None

    def set_enabled_by_installation(
        self, installation_id: str, enabled: bool, *, now_ms: int
    ) -> int:
        """Bulk-toggle every schedule owned by one App installation.

        Used when an installation is suspended or uninstalled: App Entry Point
        schedules must stop firing without per-schedule API calls, and are
        re-enabled the same way after reinstall or resume.
        """

        with self._connection() as connection:
            cursor = connection.execute(
                """UPDATE schedules SET enabled=%s,updated_at_ms=%s,
                       lease_owner=NULL,lease_until_ms=NULL
                   WHERE installation_id=%s AND enabled<>%s""",
                (enabled, now_ms, installation_id, enabled),
            )
            return int(cursor.rowcount or 0)

    def update(self, job: CronJob) -> CronJob | None:
        schedule = vars(job.schedule)
        payload = vars(job.payload)
        policy = vars(job.policy)
        p = "%s"
        schedule_value: Any = Jsonb(schedule)
        payload_value: Any = Jsonb(payload)
        schedule_column = "schedule"
        payload_column = "payload"
        query = f"""UPDATE schedules SET name={p},agent_id={p},enabled={p},
            {schedule_column}={p},{payload_column}={p},policy={p},next_run_at_ms={p},
            updated_at_ms={p},lease_owner=NULL,lease_until_ms=NULL
            WHERE schedule_id={p} AND user_id={p}"""
        params = (
            job.name,
            job.agent_id,
            job.enabled,
            schedule_value,
            payload_value,
            Jsonb(policy),
            job.state.next_run_at_ms,
            job.updated_at_ms,
            job.id,
            job.user_id,
        )
        with self._connection() as connection:
            row = connection.execute(query + " RETURNING *", params).fetchone()
        return schedule_job_from_row(row) if row else None

    def delete(self, schedule_id: str, *, user_id: str | None) -> bool:
        placeholder = "%s"
        query = f"DELETE FROM schedules WHERE schedule_id={placeholder}"
        params: list[Any] = [schedule_id]
        if user_id is not None:
            query += f" AND user_id={placeholder}"
            params.append(user_id)
        with self._connection() as connection:
            cursor = connection.execute(query, params)
            return cursor.rowcount > 0

    def db_now_ms(self) -> int:
        """Return the database wall clock in epoch milliseconds."""
        with self._connection() as connection:
            row = connection.execute(f"SELECT {_DB_NOW_MS} AS now_ms").fetchone()
        return int(row["now_ms"])

    def claim_due(
        self, *, worker_id: str, lease_ms: int, limit: int = 32
    ) -> list[CronJob]:
        query = f"""
            WITH due AS (
                SELECT schedule_id FROM schedules
                WHERE enabled AND next_run_at_ms IS NOT NULL AND next_run_at_ms<={_DB_NOW_MS}
                  AND (lease_until_ms IS NULL OR lease_until_ms<={_DB_NOW_MS})
                ORDER BY next_run_at_ms,schedule_id FOR UPDATE SKIP LOCKED LIMIT %s
            )
            UPDATE schedules s SET lease_owner=%s,lease_until_ms={_DB_NOW_MS}+%s,
                lease_version=s.lease_version+1,updated_at_ms={_DB_NOW_MS}
            FROM due WHERE s.schedule_id=due.schedule_id RETURNING s.*
            """
        with self._connection() as connection:
            rows = connection.execute(query, (limit, worker_id, lease_ms)).fetchall()
            occurrences = self._insert_occurrences(connection, rows, worker_id, lease_ms)
        return [self._claimed_schedule_job(row, occurrences[str(row["schedule_id"])]) for row in rows]

    def claim_one(
        self,
        schedule_id: str,
        *,
        worker_id: str,
        lease_ms: int,
        manual: bool = False,
    ) -> CronJob | None:
        """Claim one schedule, optionally stamping a manual occurrence for now."""
        due_condition = (
            ""
            if manual
            else f"AND enabled AND next_run_at_ms IS NOT NULL AND next_run_at_ms<={_DB_NOW_MS}"
        )
        query = f"""
            UPDATE schedules SET lease_owner=%s,lease_until_ms={_DB_NOW_MS}+%s,
                lease_version=lease_version+1,updated_at_ms={_DB_NOW_MS},
                next_run_at_ms=CASE WHEN %s THEN {_DB_NOW_MS} ELSE next_run_at_ms END
            WHERE schedule_id=%s {due_condition}
              AND (lease_until_ms IS NULL OR lease_until_ms<={_DB_NOW_MS})
            RETURNING *
            """
        with self._connection() as connection:
            row = connection.execute(
                query, (worker_id, lease_ms, manual, schedule_id)
            ).fetchone()
            rows = [row] if row else []
            occurrences = self._insert_occurrences(connection, rows, worker_id, lease_ms)
        return (
            self._claimed_schedule_job(row, occurrences[str(row["schedule_id"])])
            if row is not None
            else None
        )

    def _claimed_schedule_job(self, row: Any, occurrence: Any) -> CronJob:
        merged = dict(row)
        merged.update(
            occurrence_id=occurrence["occurrence_id"],
            scheduled_for_ms=occurrence["scheduled_for_ms"],
            attempt=occurrence["attempt"],
            submit_attempt=occurrence["submit_attempt"],
            claim_scope="schedule",
        )
        return schedule_job_from_row(merged)

    def _insert_occurrences(
        self, connection: Any, rows: list[Any], worker_id: str, lease_ms: int
    ) -> dict[str, Any]:
        occurrences: dict[str, Any] = {}
        for row in rows:
            now_ms = int(row["updated_at_ms"])
            scheduled_for = int(row["next_run_at_ms"] or now_ms)
            occurrence = connection.execute(
                f"""INSERT INTO schedule_occurrences
                       (occurrence_id,schedule_id,user_id,scheduled_for_ms,status,worker_id,
                        lease_version,name,agent_id,schedule,payload,policy,lease_owner,
                        lease_until_ms,delete_after_run,started_at_ms)
                       VALUES (%s,%s,%s,%s,'claimed',%s,%s,%s,%s,%s,%s,%s,%s,
                               {_DB_NOW_MS}+%s,%s,%s)
                       ON CONFLICT(schedule_id,scheduled_for_ms) DO UPDATE SET
                         status='claimed',worker_id=EXCLUDED.worker_id,
                         lease_version=EXCLUDED.lease_version,
                         lease_owner=EXCLUDED.lease_owner,
                         lease_until_ms=EXCLUDED.lease_until_ms,
                         error=NULL,finished_at_ms=NULL
                       RETURNING *""",
                (
                    uuid.uuid4().hex,
                    row["schedule_id"],
                    row["user_id"],
                    scheduled_for,
                    worker_id,
                    int(row["lease_version"]),
                    row["name"],
                    row["agent_id"],
                    Jsonb(row["schedule"]),
                    Jsonb(row["payload"]),
                    Jsonb(row.get("policy") or {}),
                    worker_id,
                    lease_ms,
                    bool(row["delete_after_run"]),
                    now_ms,
                ),
            ).fetchone()
            occurrences[str(row["schedule_id"])] = occurrence
        return occurrences

    def claim_due_retries(
        self, *, worker_id: str, lease_ms: int, limit: int = 32
    ) -> list[CronJob]:
        query = f"""
            WITH due AS (
                SELECT occurrence_id FROM schedule_occurrences
                WHERE status='retry_wait' AND next_attempt_at_ms<={_DB_NOW_MS}
                  AND (lease_until_ms IS NULL OR lease_until_ms<={_DB_NOW_MS})
                ORDER BY next_attempt_at_ms,occurrence_id
                FOR UPDATE SKIP LOCKED LIMIT %s
            )
            UPDATE schedule_occurrences o SET status='claimed',worker_id=%s,
                lease_owner=%s,lease_until_ms={_DB_NOW_MS}+%s,
                lease_version=o.lease_version+1,
                attempt=CASE WHEN o.run_id IS NULL THEN o.attempt ELSE o.attempt+1 END,
                submit_attempt=CASE WHEN o.run_id IS NULL THEN o.submit_attempt ELSE 0 END,
                run_id=NULL,error=NULL,next_attempt_at_ms=NULL,finished_at_ms=NULL
            FROM due WHERE o.occurrence_id=due.occurrence_id RETURNING o.*
            """
        with self._connection() as connection:
            rows = connection.execute(
                query, (limit, worker_id, worker_id, lease_ms)
            ).fetchall()
        return [occurrence_job_from_row(row) for row in rows]

    def begin_submit(self, job: CronJob, *, worker_id: str) -> CronJob | None:
        with self._connection() as connection:
            row = connection.execute(
                """UPDATE schedule_occurrences SET status='submitting',
                   submit_attempt=submit_attempt+1
                   WHERE occurrence_id=%s AND lease_owner=%s AND lease_version=%s
                   RETURNING submit_attempt""",
                (job.state.occurrence_id, worker_id, job.lease_version),
            ).fetchone()
        if row is None:
            return None
        job.state.submit_attempt = int(row["submit_attempt"])
        return job

    def has_active_occurrence(self, schedule_id: str, *, exclude_occurrence_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT 1 FROM schedule_occurrences
                   WHERE schedule_id=%s AND occurrence_id<>%s
                     AND status IN ('claimed','submitting','submitted','retry_wait')
                   LIMIT 1""",
                (schedule_id, exclude_occurrence_id),
            ).fetchone()
        return row is not None

    def has_active_runtime_session(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """Return whether user-visible work already occupies a monitor target session."""
        with self._connection() as connection:
            row = connection.execute(
                """SELECT 1 FROM runtime_runs
                   WHERE user_id=%s AND agent_id=%s AND session_id=%s
                     AND parent_run_id IS NULL
                     AND status NOT IN ('completed','failed','cancelled','timed_out')
                   LIMIT 1""",
                (user_id, agent_id, session_id),
            ).fetchone()
        return row is not None

    def renew(self, job: CronJob, *, worker_id: str, lease_ms: int) -> bool:
        with self._connection() as connection:
            occurrence = connection.execute(
                f"UPDATE schedule_occurrences SET lease_until_ms={_DB_NOW_MS}+%s WHERE occurrence_id=%s AND lease_owner=%s AND lease_version=%s",
                (lease_ms, job.state.occurrence_id, worker_id, job.lease_version),
            )
            if job.state.claim_scope == "occurrence":
                return occurrence.rowcount > 0
            schedule = connection.execute(
                f"UPDATE schedules SET lease_until_ms={_DB_NOW_MS}+%s WHERE schedule_id=%s AND lease_owner=%s AND lease_version=%s",
                (lease_ms, job.id, worker_id, job.lease_version),
            )
            return occurrence.rowcount > 0 and schedule.rowcount > 0

    def finish(
        self,
        job: CronJob,
        *,
        worker_id: str,
        status: str,
        error: str | None,
        run_id: str | None,
        next_run_at_ms: int | None,
        enabled: bool,
        finished_at_ms: int,
        next_attempt_at_ms: int | None = None,
        delivery_content: str | None = None,
    ) -> bool:
        with self._connection() as connection:
            # Fence the occurrence before advancing the schedule cursor or
            # inserting an outbox row. Returning after a partial update would
            # otherwise strand an unlinked Run if this worker lost its lease.
            fenced = connection.execute(
                """SELECT occurrence_id FROM schedule_occurrences
                   WHERE occurrence_id=%s AND lease_owner=%s AND lease_version=%s
                   FOR UPDATE""",
                (job.state.occurrence_id, worker_id, job.lease_version),
            ).fetchone()
            if fenced is None:
                return False
            if job.state.claim_scope == "schedule":
                connection.execute(
                    """UPDATE schedules SET last_run_at_ms=%s,last_status=%s,last_error=%s,
                       next_run_at_ms=%s,enabled=%s,lease_owner=NULL,lease_until_ms=NULL,
                       updated_at_ms=%s
                       WHERE schedule_id=%s AND lease_owner=%s AND lease_version=%s""",
                    (
                        finished_at_ms,
                        status,
                        error,
                        next_run_at_ms,
                        enabled,
                        finished_at_ms,
                        job.id,
                        worker_id,
                        job.lease_version,
                    ),
                )
            terminal = status not in {"submitted", "retry_wait"}
            delivery_status: str | None = None
            delivery_outbound_id: str | None = None
            delivery_error: str | None = None
            if delivery_content is not None:
                (
                    delivery_status,
                    delivery_outbound_id,
                    delivery_error,
                ) = enqueue_schedule_delivery(
                    connection,
                    occurrence_id=str(job.state.occurrence_id),
                    schedule_id=job.id,
                    user_id=job.user_id,
                    payload=vars(job.payload),
                    content=delivery_content,
                    run_id=run_id,
                    attempt=job.state.attempt,
                )
            row = connection.execute(
                """UPDATE schedule_occurrences SET status=%s,run_id=COALESCE(%s,run_id),
                   error=%s,next_attempt_at_ms=%s,
                   finished_at_ms=CASE WHEN %s THEN %s ELSE NULL END,
                   delivery_status=COALESCE(%s,delivery_status),
                   delivery_outbound_id=COALESCE(%s,delivery_outbound_id),
                   delivery_error=%s,
                   lease_owner=NULL,lease_until_ms=NULL
                   WHERE occurrence_id=%s AND lease_owner=%s AND lease_version=%s
                   RETURNING occurrence_id""",
                (
                    status,
                    run_id,
                    error,
                    next_attempt_at_ms,
                    terminal,
                    finished_at_ms,
                    delivery_status,
                    delivery_outbound_id,
                    delivery_error,
                    job.state.occurrence_id,
                    worker_id,
                    job.lease_version,
                ),
            ).fetchone()
            if row is None:
                return False
            if run_id:
                connection.execute(
                    f"""INSERT INTO schedule_occurrence_runs(
                           occurrence_id,run_id,attempt,submitted_at_ms
                       ) VALUES (%s,%s,%s,{_DB_NOW_MS})
                       ON CONFLICT(occurrence_id,run_id) DO NOTHING""",
                    (job.state.occurrence_id, run_id, job.state.attempt),
                )
            if status == "submitted" and run_id:
                runtime_run = connection.execute(
                    """UPDATE runtime_runs SET options=jsonb_set(
                           options,
                           '{metadata}',
                           COALESCE(options->'metadata','{}'::jsonb)
                             || '{"_runtime_schedule_submission_ready":true}'::jsonb,
                           TRUE
                       )
                       WHERE run_id=%s
                       RETURNING status,result,error""",
                    (run_id,),
                ).fetchone()
                if runtime_run is not None:
                    connection.execute(
                        "SELECT pg_notify('porthouse_runtime_work',%s)", (run_id,)
                    )
                    if runtime_run["status"] in {
                        "completed",
                        "failed",
                        "cancelled",
                        "timed_out",
                    }:
                        project_schedule_run_terminal(
                            connection,
                            run_id=run_id,
                            status=str(runtime_run["status"]),
                            result=runtime_run["result"],
                            error=runtime_run["error"],
                        )
            if terminal and job.delete_after_run:
                connection.execute("DELETE FROM schedules WHERE schedule_id=%s", (job.id,))
            return True

    def list_occurrences(
        self, *, user_id: str, schedule_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        p = "%s"
        query = f"""SELECT occurrence.*,
                         COALESCE((
                           SELECT array_agg(link.run_id ORDER BY
                               link.attempt,link.submitted_at_ms,link.run_id)
                           FROM schedule_occurrence_runs link
                           WHERE link.occurrence_id=occurrence.occurrence_id
                         ),ARRAY[]::text[]) AS linked_run_ids
                     FROM schedule_occurrences occurrence
                     WHERE occurrence.user_id={p}"""
        params: list[Any] = [user_id]
        if schedule_id:
            query += f" AND occurrence.schedule_id={p}"
            params.append(schedule_id)
        query += f" ORDER BY occurrence.started_at_ms DESC LIMIT {p}"
        params.append(limit)
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                "id": row["occurrence_id"],
                "jobId": row["schedule_id"],
                "userId": row["user_id"],
                "status": row["status"],
                "runId": row["run_id"],
                "runIds": list(row["linked_run_ids"] or []),
                "attempt": int(row["attempt"] or 1),
                "submitAttempt": int(row["submit_attempt"] or 0),
                "scheduledForMs": row["scheduled_for_ms"],
                "nextAttemptAtMs": row["next_attempt_at_ms"],
                "error": row["error"],
                "deliveryStatus": row["delivery_status"],
                "deliveryOutboundId": row["delivery_outbound_id"],
                "deliveryError": row["delivery_error"],
                "deliveredAtMs": row["delivered_at_ms"],
                "monitorScratchRevision": row["monitor_scratch_revision"],
                "monitorObservationHash": row["monitor_observation_hash"],
                "monitorPreflightStatus": row["monitor_preflight_status"],
                "monitorObservation": json.loads(row["monitor_observation"])
                if isinstance(row["monitor_observation"], str)
                else dict(row["monitor_observation"] or {}),
                "startedAtMs": row["started_at_ms"],
                "finishedAtMs": row["finished_at_ms"],
            }
            for row in rows
        ]
