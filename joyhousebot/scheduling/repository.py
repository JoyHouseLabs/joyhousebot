"""PostgreSQL repository for schedules and occurrences."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from joyhousebot.cron.types import CronJob, CronJobState, CronPayload, CronSchedule


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
        ddl = """
            CREATE TABLE IF NOT EXISTS schedules (
                schedule_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                agent_id TEXT,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                schedule JSONB NOT NULL,
                payload JSONB NOT NULL,
                next_run_at_ms BIGINT,
                last_run_at_ms BIGINT,
                last_status TEXT,
                last_error TEXT,
                delete_after_run BOOLEAN NOT NULL DEFAULT FALSE,
                lease_owner TEXT,
                lease_until_ms BIGINT,
                lease_version BIGINT NOT NULL DEFAULT 0,
                created_at_ms BIGINT NOT NULL,
                updated_at_ms BIGINT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_schedules_due
                ON schedules(next_run_at_ms, schedule_id)
                WHERE enabled AND next_run_at_ms IS NOT NULL;
            CREATE INDEX IF NOT EXISTS ix_schedules_user
                ON schedules(user_id, updated_at_ms DESC);
            CREATE TABLE IF NOT EXISTS schedule_occurrences (
                occurrence_id TEXT PRIMARY KEY,
                schedule_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                scheduled_for_ms BIGINT NOT NULL,
                status TEXT NOT NULL,
                worker_id TEXT,
                lease_version BIGINT NOT NULL,
                run_id TEXT,
                error TEXT,
                started_at_ms BIGINT NOT NULL,
                finished_at_ms BIGINT,
                UNIQUE(schedule_id, scheduled_for_ms)
            );
            CREATE INDEX IF NOT EXISTS ix_schedule_occurrences_user
                ON schedule_occurrences(user_id, started_at_ms DESC);
            """
        with self.store._pool.connection() as connection:
            with connection.transaction():
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (872341911,))
                connection.execute(ddl)

    @staticmethod
    def _job(row: Any) -> CronJob:
        schedule = row["schedule"] if "schedule" in row else row["schedule_json"]
        payload = row["payload"] if "payload" in row else row["payload_json"]
        if isinstance(schedule, str):
            schedule = json.loads(schedule)
        if isinstance(payload, str):
            payload = json.loads(payload)
        return CronJob(
            id=str(row["schedule_id"]),
            name=str(row["name"]),
            user_id=str(row["user_id"]),
            enabled=bool(row["enabled"]),
            agent_id=row["agent_id"],
            schedule=CronSchedule(**schedule),
            payload=CronPayload(**payload),
            state=CronJobState(
                next_run_at_ms=row["next_run_at_ms"],
                last_run_at_ms=row["last_run_at_ms"],
                last_status=row["last_status"],
                last_error=row["last_error"],
            ),
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
            delete_after_run=bool(row["delete_after_run"]),
            lease_owner=row["lease_owner"],
            lease_until_ms=row["lease_until_ms"],
            lease_version=int(row["lease_version"]),
        )

    def create(self, job: CronJob) -> CronJob:
        schedule = vars(job.schedule)
        payload = vars(job.payload)
        columns = """schedule_id,user_id,name,agent_id,enabled,schedule,payload,
            next_run_at_ms,last_run_at_ms,last_status,last_error,delete_after_run,
            lease_owner,lease_until_ms,lease_version,created_at_ms,updated_at_ms"""
        from psycopg.types.json import Jsonb

        values = (
                job.id,
                job.user_id,
                job.name,
                job.agent_id,
                job.enabled,
                Jsonb(schedule),
                Jsonb(payload),
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
                f"INSERT INTO schedules ({columns}) VALUES ({','.join(['%s'] * 17)}) RETURNING *",
                values,
            ).fetchone()
        return self._job(row)

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
        return [self._job(row) for row in rows]

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
        return self._job(row) if row else None

    def update(self, job: CronJob) -> CronJob | None:
        schedule = vars(job.schedule)
        payload = vars(job.payload)
        p = "%s"
        from psycopg.types.json import Jsonb

        schedule_value: Any = Jsonb(schedule)
        payload_value: Any = Jsonb(payload)
        schedule_column = "schedule"
        payload_column = "payload"
        query = f"""UPDATE schedules SET name={p},agent_id={p},enabled={p},
            {schedule_column}={p},{payload_column}={p},next_run_at_ms={p},
            updated_at_ms={p},lease_owner=NULL,lease_until_ms=NULL
            WHERE schedule_id={p} AND user_id={p}"""
        params = (
            job.name,
            job.agent_id,
            job.enabled,
            schedule_value,
            payload_value,
            job.state.next_run_at_ms,
            job.updated_at_ms,
            job.id,
            job.user_id,
        )
        with self._connection() as connection:
            row = connection.execute(query + " RETURNING *", params).fetchone()
        return self._job(row) if row else None

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

    def claim_due(
        self, *, worker_id: str, now_ms: int, lease_ms: int, limit: int = 32
    ) -> list[CronJob]:
        query = """
            WITH due AS (
                SELECT schedule_id FROM schedules
                WHERE enabled AND next_run_at_ms IS NOT NULL AND next_run_at_ms<=%s
                  AND (lease_until_ms IS NULL OR lease_until_ms<=%s)
                ORDER BY next_run_at_ms,schedule_id FOR UPDATE SKIP LOCKED LIMIT %s
            )
            UPDATE schedules s SET lease_owner=%s,lease_until_ms=%s,
                lease_version=s.lease_version+1,updated_at_ms=%s
            FROM due WHERE s.schedule_id=due.schedule_id RETURNING s.*
            """
        with self._connection() as connection:
            rows = connection.execute(
                query, (now_ms, now_ms, limit, worker_id, now_ms + lease_ms, now_ms)
            ).fetchall()
            self._insert_occurrences(connection, rows, worker_id, now_ms)
        return [self._job(row) for row in rows]

    def claim_one(
        self,
        schedule_id: str,
        *,
        worker_id: str,
        now_ms: int,
        lease_ms: int,
        manual: bool = False,
    ) -> CronJob | None:
        """Claim exactly one requested schedule without consuming unrelated work."""
        due_condition = (
            ""
            if manual
            else "AND enabled AND next_run_at_ms IS NOT NULL AND next_run_at_ms<=%s"
        )
        query = f"""
            UPDATE schedules SET lease_owner=%s,lease_until_ms=%s,
                lease_version=lease_version+1,updated_at_ms=%s,
                next_run_at_ms=CASE WHEN %s THEN %s ELSE next_run_at_ms END
            WHERE schedule_id=%s {due_condition}
              AND (lease_until_ms IS NULL OR lease_until_ms<=%s)
            RETURNING *
            """
        params: list[Any] = [
                worker_id,
                now_ms + lease_ms,
                now_ms,
                manual,
                now_ms,
                schedule_id,
            ]
        if not manual:
            params.append(now_ms)
        params.append(now_ms)
        with self._connection() as connection:
            row = connection.execute(query, params).fetchone()
            rows = [row] if row else []
            self._insert_occurrences(connection, rows, worker_id, now_ms)
        return self._job(row) if row else None

    def _insert_occurrences(
        self, connection: Any, rows: list[Any], worker_id: str, now_ms: int
    ) -> None:
        for row in rows:
            scheduled_for = int(row["next_run_at_ms"] or now_ms)
            values = (
                uuid.uuid4().hex,
                row["schedule_id"],
                row["user_id"],
                scheduled_for,
                "running",
                worker_id,
                int(row["lease_version"]),
                now_ms,
            )
            connection.execute(
                """INSERT INTO schedule_occurrences
                       (occurrence_id,schedule_id,user_id,scheduled_for_ms,status,worker_id,lease_version,started_at_ms)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(schedule_id,scheduled_for_ms) DO UPDATE SET
                         status='running',worker_id=EXCLUDED.worker_id,
                         lease_version=EXCLUDED.lease_version,run_id=NULL,error=NULL,
                         started_at_ms=EXCLUDED.started_at_ms,finished_at_ms=NULL""",
                values,
            )

    def renew(self, job: CronJob, *, worker_id: str, now_ms: int, lease_ms: int) -> bool:
        p = "%s"
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE schedules SET lease_until_ms={p} WHERE schedule_id={p} AND lease_owner={p} AND lease_version={p}",
                (now_ms + lease_ms, job.id, worker_id, job.lease_version),
            )
            return cursor.rowcount > 0

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
    ) -> bool:
        p = "%s"
        with self._connection() as connection:
            cursor = connection.execute(
                f"""UPDATE schedules SET last_run_at_ms={p},last_status={p},last_error={p},
                    next_run_at_ms={p},enabled={p},lease_owner=NULL,lease_until_ms=NULL,updated_at_ms={p}
                    WHERE schedule_id={p} AND lease_owner={p} AND lease_version={p}""",
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
            if cursor.rowcount == 0:
                return False
            connection.execute(
                f"""UPDATE schedule_occurrences SET status={p},run_id={p},error={p},finished_at_ms={p}
                    WHERE schedule_id={p} AND scheduled_for_ms={p} AND lease_version={p}""",
                (
                    status,
                    run_id,
                    error,
                    finished_at_ms,
                    job.id,
                    int(job.state.next_run_at_ms or finished_at_ms),
                    job.lease_version,
                ),
            )
            if job.delete_after_run:
                connection.execute(f"DELETE FROM schedules WHERE schedule_id={p}", (job.id,))
            return True

    def list_occurrences(
        self, *, user_id: str, schedule_id: str | None, limit: int
    ) -> list[dict[str, Any]]:
        p = "%s"
        query = f"SELECT * FROM schedule_occurrences WHERE user_id={p}"
        params: list[Any] = [user_id]
        if schedule_id:
            query += f" AND schedule_id={p}"
            params.append(schedule_id)
        query += f" ORDER BY started_at_ms DESC LIMIT {p}"
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
                "error": row["error"],
                "startedAtMs": row["started_at_ms"],
                "finishedAtMs": row["finished_at_ms"],
            }
            for row in rows
        ]
