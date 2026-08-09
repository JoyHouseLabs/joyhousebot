"""PostgreSQL runtime-run persistence and lease transitions."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from joyhousebot.runtime.models import AgentEvent
from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.postgres_artifact_writes import (
    insert_runtime_artifact_in_transaction,
)
from joyhousebot.storage.postgres_channel_callbacks import project_channel_run_terminal
from joyhousebot.storage.postgres_event_writes import append_runtime_event_in_transaction
from joyhousebot.storage.postgres_quotas import check_top_level_submission_quota
from joyhousebot.storage.postgres_schedule_callbacks import project_schedule_run_terminal
from joyhousebot.storage.runtime_store import (
    RuntimeRunRecord,
)

_CHANNEL = "joyhousebot_runtime_work"
_TERMINAL = ("completed", "failed", "cancelled", "timed_out")
_TASK_TERMINAL = (*_TERMINAL, "skipped")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


class PostgresRunStoreMixin:
    def create_runtime_run(
        self,
        *,
        run_id: str,
        user_id: str,
        session_id: str,
        agent_id: str = "default",
        kind: str,
        prompt: str,
        options: dict[str, Any],
        idempotency_key: str | None = None,
        root_run_id: str | None = None,
        parent_run_id: str | None = None,
        parent_task_id: str | None = None,
        total_task_count: int = 0,
        initial_status: str = "queued",
        max_children_per_root: int | None = None,
        max_active_per_user: int | None = None,
        max_submissions_per_minute: int | None = None,
    ) -> tuple[RuntimeRunRecord, bool]:
        with self._pool.connection() as conn, conn.transaction():
            if parent_run_id is None and root_run_id is None:
                existing = check_top_level_submission_quota(
                    conn,
                    user_id=user_id,
                    agent_id=agent_id,
                    session_id=session_id,
                    idempotency_key=idempotency_key,
                    max_active_per_user=max_active_per_user,
                    max_submissions_per_minute=max_submissions_per_minute,
                )
                if existing is not None:
                    return self._run(existing), False
            if root_run_id and parent_run_id and max_children_per_root is not None:
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (root_run_id,))
                existing = None
                if idempotency_key:
                    existing = conn.execute(
                        """SELECT *,FALSE AS created FROM runtime_runs
                           WHERE user_id=%s AND agent_id=%s AND session_id=%s
                             AND idempotency_key=%s""",
                        (user_id, agent_id, session_id, idempotency_key),
                    ).fetchone()
                if existing is not None:
                    return self._run(existing), False
                child_count = conn.execute(
                    """SELECT COUNT(*) AS count FROM runtime_runs
                       WHERE root_run_id=%s AND parent_run_id IS NOT NULL""",
                    (root_run_id,),
                ).fetchone()
                if int(child_count["count"]) >= max(0, int(max_children_per_root)):
                    raise RuntimeError(f"child run fan-out limit reached ({max_children_per_root})")
            row = conn.execute(
                """
                INSERT INTO runtime_runs (
                    run_id, user_id, session_id, agent_id, kind, status, prompt, options,
                    idempotency_key, root_run_id, parent_run_id, parent_task_id,total_task_count
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,%s)
                ON CONFLICT (user_id, agent_id, session_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL DO NOTHING
                RETURNING *, TRUE AS created
                """,
                (
                    run_id,
                    user_id,
                    session_id,
                    agent_id,
                    kind,
                    initial_status,
                    prompt,
                    Jsonb(options),
                    idempotency_key,
                    root_run_id or run_id,
                    parent_run_id,
                    parent_task_id,
                    max(0, int(total_task_count)),
                ),
            ).fetchone()
            if row is None:
                if idempotency_key is None:
                    raise RuntimeError(f"runtime run already exists: {run_id}")
                row = conn.execute(
                    """SELECT *,FALSE AS created FROM runtime_runs
                       WHERE user_id=%s AND agent_id=%s AND session_id=%s
                         AND idempotency_key=%s""",
                    (user_id, agent_id, session_id, idempotency_key),
                ).fetchone()
            assert row is not None
            if row["created"]:
                self._audit(
                    conn,
                    run_id=str(row["run_id"]),
                    stage="store.run.created",
                    message="Run committed",
                )
                self._notify(conn, str(row["run_id"]))
            return self._run(row), bool(row["created"])

    def get_runtime_run(
        self, run_id: str, expected_user_id: str | None = None
    ) -> RuntimeRunRecord | None:
        clauses = ["run_id=%s"]
        params: list[Any] = [run_id]
        if expected_user_id is not None:
            clauses.append("user_id=%s")
            params.append(expected_user_id)
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT * FROM runtime_runs WHERE {' AND '.join(clauses)}", params
            ).fetchone()
        return self._run(row) if row else None

    def claim_runtime_run(
        self, run_id: str, *, worker_id: str, lease_seconds: int = 30
    ) -> RuntimeRunRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            identity = conn.execute(
                "SELECT user_id, session_id, agent_id, parent_run_id FROM runtime_runs WHERE run_id=%s",
                (run_id,),
            ).fetchone()
            if identity is None:
                return None
            if identity["parent_run_id"] is None:
                # A row lock cannot prevent two workers claiming two different
                # queued rows for the same conversation.  The transaction-level
                # advisory lock closes that write-skew window cluster-wide.
                # The short user/session lock also coordinates session deletion
                # without preventing different agents from executing in parallel.
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 731947))",
                    (f"{identity['user_id']}\x1f{identity['session_id']}",),
                )
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 731947))",
                    (
                        f"{identity['user_id']}\x1f{identity['agent_id']}"
                        f"\x1f{identity['session_id']}",
                    ),
                )
            row = conn.execute(
                """
                WITH candidate AS (
                    SELECT pending.run_id FROM runtime_runs pending
                    WHERE pending.run_id=%s AND pending.cancel_requested_at IS NULL AND (
                        pending.status='queued' OR (pending.status='running' AND
                        (pending.lease_owner IS NULL OR pending.lease_expires_at IS NULL
                         OR pending.lease_expires_at < clock_timestamp())) OR
                        (pending.status='waiting_external' AND EXISTS (
                           SELECT 1 FROM operation_reconciliations rec
                           WHERE rec.run_id=pending.run_id AND
                             ((rec.status='pending' AND rec.next_attempt_at<=clock_timestamp())
                              OR (rec.status='checking' AND rec.lease_expires_at<clock_timestamp()))))
                    ) AND (
                      COALESCE(
                        (pending.options->'metadata'->>'_runtime_initial_events_required')::boolean,
                        FALSE
                      ) = FALSE
                      OR EXISTS (
                        SELECT 1 FROM runtime_events ready
                        WHERE ready.run_id=pending.run_id
                          AND ready.event_type='run.queued'
                      )
                    ) AND (
                      COALESCE(
                        pending.options->'metadata'->>'_runtime_schedule_submission_ready',
                        'true'
                      ) <> 'false'
                    ) AND (
                      pending.kind!='graph' OR NOT EXISTS (
                        SELECT 1 FROM runtime_tasks graph_task
                        WHERE graph_task.run_id=pending.run_id
                          AND graph_task.status IN (
                            'queued','blocked','running','waiting_approval','waiting_external'
                          )
                      )
                    ) AND (
                      pending.parent_run_id IS NOT NULL OR NOT EXISTS (
                        SELECT 1 FROM runtime_runs active
                        WHERE active.user_id=pending.user_id
                          AND active.session_id=pending.session_id
                          AND active.agent_id=pending.agent_id
                          AND active.run_id<>pending.run_id
                          AND active.parent_run_id IS NULL
                          AND (
                            active.status='running'
                            OR (
                              active.status IN ('queued','planning')
                              AND (active.created_at,active.run_id)
                                  < (pending.created_at,pending.run_id)
                            )
                          )
                      )
                    ) FOR UPDATE SKIP LOCKED
                )
                UPDATE runtime_runs r SET status='running', lease_owner=%s,
                    lease_expires_at=clock_timestamp() + (%s * interval '1 second'),
                    lease_version=r.lease_version + 1,
                    started_at=COALESCE(r.started_at, clock_timestamp()),
                    updated_at=clock_timestamp()
                FROM candidate c WHERE r.run_id=c.run_id RETURNING r.*
                """,
                (run_id, worker_id, max(5, lease_seconds)),
            ).fetchone()
            if row:
                self._audit(
                    conn,
                    run_id=run_id,
                    worker_id=worker_id,
                    stage="store.run.claimed",
                    message="Run lease acquired",
                    data={"lease_version": int(row["lease_version"])},
                )
        return self._run(row) if row else None

    def heartbeat_runtime_run(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 30,
        lease_version: int | None = None,
    ) -> bool:
        with self._pool.connection() as conn:
            # Only a live lease may be renewed: once lease_expires_at has
            # passed the heartbeat must fail so a zombie worker takes the
            # lease-lost path instead of extending a double-execution window.
            cur = conn.execute(
                """UPDATE runtime_runs SET
                       lease_expires_at=clock_timestamp() + (%s * interval '1 second'),
                       updated_at=clock_timestamp()
                   WHERE run_id=%s AND status='running' AND lease_owner=%s
                     AND lease_expires_at >= clock_timestamp()
                     AND cancel_requested_at IS NULL
                     AND (%s::bigint IS NULL OR lease_version=%s)""",
                (max(5, lease_seconds), run_id, worker_id, lease_version, lease_version),
            )
            return cur.rowcount == 1

    def list_runtime_runs(
        self,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        root_run_id: str | None = None,
        parent_run_id: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RuntimeRunRecord]:
        clauses, params = [], []
        if user_id:
            clauses.append("user_id=%s")
            params.append(user_id)
        if session_id:
            clauses.append("session_id=%s")
            params.append(session_id)
        if agent_id:
            clauses.append("agent_id=%s")
            params.append(agent_id)
        if status:
            clauses.append("status=%s")
            params.append(status)
        if root_run_id:
            clauses.append("root_run_id=%s")
            params.append(root_run_id)
        if parent_run_id:
            clauses.append("parent_run_id=%s")
            params.append(parent_run_id)
        if search and search.strip():
            pattern = f"%{search.strip()}%"
            clauses.append(
                "(run_id ILIKE %s OR session_id ILIKE %s OR agent_id ILIKE %s "
                "OR COALESCE(status_summary, '') ILIKE %s OR COALESCE(prompt, '') ILIKE %s)"
            )
            params.extend([pattern] * 5)
        query = "SELECT * FROM runtime_runs"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params.extend([max(1, min(1000, limit)), max(0, min(100_000, offset))])
        with self._pool.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._run(row) for row in rows]

    def delete_runtime_session(
        self, *, user_id: str, session_id: str, agent_id: str | None = None
    ) -> int:
        """Delete a user's terminal session under a conversation advisory lock.

        Also clears per-session leftovers not covered by FK cascades:
        conversation session state and request trace events of deleted runs.
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 731947))",
                (f"{user_id}\x1f{session_id}",),
            )
            params: list[Any] = [user_id, session_id]
            agent_clause = ""
            if agent_id:
                agent_clause = " AND agent_id=%s"
                params.append(agent_id)
            active = conn.execute(
                "SELECT 1 FROM runtime_runs WHERE user_id=%s AND session_id=%s"
                + agent_clause
                + " AND status IN ('queued','running') LIMIT 1",
                params,
            ).fetchone()
            if active is not None:
                raise ValueError("active session runs must be cancelled before deletion")
            conn.execute(
                "DELETE FROM request_trace_events WHERE run_id IN "
                "(SELECT run_id FROM runtime_runs WHERE user_id=%s AND session_id=%s"
                + agent_clause
                + ")",
                params,
            )
            session_params: list[Any] = [session_id]
            namespace_clause = ""
            if agent_id:
                namespace_clause = " AND namespace=%s"
                session_params.append(agent_id)
            conn.execute(
                "DELETE FROM conversation_sessions WHERE session_key=%s" + namespace_clause,
                session_params,
            )
            deleted = conn.execute(
                "DELETE FROM runtime_runs WHERE user_id=%s AND session_id=%s" + agent_clause,
                params,
            )
            return max(0, deleted.rowcount)

    def list_incomplete_runtime_runs(self, limit: int = 500) -> list[RuntimeRunRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM (
                       SELECT runtime_runs.*,
                              ROW_NUMBER() OVER (
                                  PARTITION BY user_id ORDER BY created_at, run_id
                              ) AS user_queue_position
                       FROM runtime_runs
                       WHERE status IN ('queued','planning','running') OR
                         (status='waiting_external' AND EXISTS (
                            SELECT 1 FROM operation_reconciliations rec
                            WHERE rec.run_id=runtime_runs.run_id AND
                              ((rec.status='pending' AND rec.next_attempt_at<=clock_timestamp())
                               OR (rec.status='checking' AND rec.lease_expires_at<clock_timestamp()))))
                         OR (status='waiting_external' AND EXISTS (
                            SELECT 1 FROM graph_event_waits event_wait
                            WHERE event_wait.run_id=runtime_runs.run_id
                              AND event_wait.status='pending'
                              AND event_wait.deadline_at<=clock_timestamp()))
                   ) AS fair_queue
                   ORDER BY user_queue_position, created_at, run_id LIMIT %s""",
                (max(1, min(5000, limit)),),
            ).fetchall()
        return [self._run(row) for row in rows]

    def update_runtime_run(
        self,
        run_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        worker_id: str | None = None,
        lease_version: int | None = None,
    ) -> bool:
        terminal = status in _TERMINAL
        release_lease = terminal or status in {
            "waiting_input",
            "waiting_approval",
            "waiting_external",
            "paused",
            "scheduled",
        }
        with self._pool.connection() as conn, conn.transaction():
            cur = conn.execute(
                """
                UPDATE runtime_runs SET status=%s, result=COALESCE(%s, result), error=%s,
                    started_at=CASE WHEN %s='running' THEN COALESCE(started_at, clock_timestamp()) ELSE started_at END,
                    finished_at=CASE WHEN %s THEN clock_timestamp() ELSE finished_at END,
                    lease_owner=CASE WHEN %s THEN NULL ELSE lease_owner END,
                    lease_expires_at=CASE WHEN %s THEN NULL ELSE lease_expires_at END,
                    updated_at=clock_timestamp()
                WHERE run_id=%s
                  AND (status NOT IN ('completed','failed','cancelled','timed_out') OR status=%s)
                  AND (%s::text<>'running' OR cancel_requested_at IS NULL)
                  AND (%s::text IS NULL OR lease_owner=%s)
                  AND (%s::bigint IS NULL OR lease_version=%s)
                """,
                (
                    status,
                    Jsonb(result) if result is not None else None,
                    Jsonb(error) if error is not None else None,
                    status,
                    terminal,
                    release_lease,
                    release_lease,
                    run_id,
                    status,
                    status,
                    worker_id,
                    worker_id,
                    lease_version,
                    lease_version,
                ),
            )
            if cur.rowcount:
                self._audit(
                    conn,
                    run_id=run_id,
                    worker_id=worker_id,
                    stage="store.run.transition",
                    message=f"Run transitioned to {status}",
                    level="error" if status == "failed" else "info",
                    data={"status": status, "lease_version": lease_version},
                )
                self._notify(conn, run_id)
            return cur.rowcount == 1

    def finish_runtime_run(
        self,
        run_id: str,
        *,
        status: str,
        event: AgentEvent,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        worker_id: str | None = None,
        lease_version: int | None = None,
    ) -> AgentEvent | None:
        """Compatibility entry point returning the terminal event only."""
        bundle = self.finish_runtime_run_bundle(
            run_id,
            status=status,
            event=event,
            result=result,
            error=error,
            artifacts=artifacts,
            worker_id=worker_id,
            lease_version=lease_version,
        )
        return bundle[1] if bundle is not None else None

    def finish_runtime_run_bundle(
        self,
        run_id: str,
        *,
        status: str,
        event: AgentEvent,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        events_before_terminal: list[AgentEvent] | None = None,
        worker_id: str | None = None,
        lease_version: int | None = None,
    ) -> tuple[list[AgentEvent], AgentEvent] | None:
        """Atomically commit output events, Artifacts, and terminal state."""
        if status not in _TERMINAL:
            raise ValueError("finish_runtime_run requires a terminal status")
        persisted_before: list[AgentEvent] = []
        with self._pool.connection() as conn, conn.transaction():
            persisted = self._finish_runtime_run_in_transaction(
                conn,
                run_id=run_id,
                status=status,
                event=event,
                result=result,
                error=error,
                events_before_terminal=events_before_terminal,
                persisted_events_before_terminal=persisted_before,
                worker_id=worker_id,
                lease_version=lease_version,
            )
            if persisted is None:
                return None
            for artifact in artifacts or []:
                insert_runtime_artifact_in_transaction(
                    conn,
                    **{**artifact, "run_id": run_id},
                )
            self._audit(
                conn,
                run_id=run_id,
                worker_id=worker_id,
                stage="store.run.finished",
                message=f"Run and terminal event committed as {status}",
                level="error" if status == "failed" else "info",
                data={
                    "status": status,
                    "lease_version": lease_version,
                    "event_id": persisted.event_id,
                },
            )
            self._notify(conn, run_id)
        return persisted_before, persisted

    def _finish_runtime_run_in_transaction(
        self,
        conn: Any,
        *,
        run_id: str,
        status: str,
        event: AgentEvent,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        events_before_terminal: list[AgentEvent] | None = None,
        persisted_events_before_terminal: list[AgentEvent] | None = None,
        worker_id: str | None = None,
        lease_version: int | None = None,
        force_fence: bool = False,
    ) -> AgentEvent | None:
        """Terminal transition primitive shared by workers and approval recovery."""
        if status not in _TERMINAL:
            raise ValueError("terminal Run transition requires a terminal status")
        updated = conn.execute(
                """
                UPDATE runtime_runs SET status=%s, result=COALESCE(%s,result), error=%s,
                    finished_at=clock_timestamp(), lease_owner=NULL, lease_expires_at=NULL,
                    lease_version=CASE WHEN %s THEN lease_version+1 ELSE lease_version END,
                    status_summary=COALESCE(%s,status_summary),
                    status_reason=COALESCE(%s,status_reason), waiting_on=NULL,
                    active_span_count=0, updated_at=clock_timestamp()
                WHERE run_id=%s
                  AND status NOT IN ('completed','failed','cancelled','timed_out')
                  AND (%s OR (
                    (%s::text IS NOT NULL AND lease_owner=%s)
                    OR (%s::text IS NULL AND (
                      lease_owner IS NULL OR lease_expires_at IS NULL
                      OR lease_expires_at < clock_timestamp()))
                  ))
                  AND (%s OR %s::bigint IS NULL OR lease_version=%s)
                RETURNING run_id,user_id,options
                """,
                (
                    status,
                    Jsonb(result) if result is not None else None,
                    Jsonb(error) if error is not None else None,
                    force_fence,
                    event.summary,
                    event.data.get("error") or event.data.get("reason"),
                    run_id,
                    force_fence,
                    worker_id,
                    worker_id,
                    worker_id,
                    force_fence,
                    lease_version,
                    lease_version,
                ),
            ).fetchone()
        if updated is None:
            return None
        project_schedule_run_terminal(
            conn,
            run_id=run_id,
            status=status,
            result=result,
            error=error,
        )
        project_channel_run_terminal(
            conn,
            run_id=run_id,
            user_id=str(updated["user_id"]),
            status=status,
            options=updated["options"],
            result=result,
            error=error,
        )
        for prior_event in events_before_terminal or []:
            prior = append_runtime_event_in_transaction(conn, prior_event)
            if persisted_events_before_terminal is not None:
                persisted_events_before_terminal.append(prior)
        return append_runtime_event_in_transaction(conn, event)
