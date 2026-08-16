"""PostgreSQL state machine for token-authenticated Graph external events."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema.validators import validator_for

from porthouse.storage.graph_event_records import GraphEventWaitRecord
from porthouse.storage.json_codec import Jsonb


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PostgresGraphWaitEventStoreMixin:
    def migrate_graph_event_waits(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS graph_event_waits (
            wait_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT NOT NULL REFERENCES runtime_tasks(task_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_schema JSONB NOT NULL,
            config_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            token_hash TEXT,
            token_version INTEGER NOT NULL DEFAULT 0,
            token_issued_at TIMESTAMPTZ,
            deadline_at TIMESTAMPTZ NOT NULL,
            payload JSONB,
            payload_hash TEXT,
            received_at TIMESTAMPTZ,
            received_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_graph_event_waits_pending_task
            ON graph_event_waits(task_id) WHERE status='pending';
        CREATE INDEX IF NOT EXISTS ix_graph_event_waits_owner_run
            ON graph_event_waits(user_id,run_id,created_at);
        CREATE INDEX IF NOT EXISTS ix_graph_event_waits_due
            ON graph_event_waits(deadline_at) WHERE status='pending';
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341929,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="graph_event_waits",
                version=1,
                ddl=ddl,
                description="token-authenticated durable Graph external event waits",
            )

    def suspend_graph_task_for_event(self, **kwargs: Any) -> GraphEventWaitRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            task = conn.execute(
                """SELECT task.*,run.user_id FROM runtime_tasks task
                   JOIN runtime_runs run ON run.run_id=task.run_id
                   WHERE task.task_id=%s FOR UPDATE OF task,run""",
                (kwargs["task_id"],),
            ).fetchone()
            if (
                task is None
                or str(task["run_id"]) != kwargs["run_id"]
                or str(task["status"]) != "running"
                or str(task["lease_owner"] or "") != kwargs["worker_id"]
                or int(task["lease_version"]) != int(kwargs["lease_version"])
                or str(task["payload"].get("node_type") or "") != "wait_event"
            ):
                return None
            row = conn.execute(
                """INSERT INTO graph_event_waits
                       (wait_id,run_id,task_id,user_id,event_type,payload_schema,
                        config_hash,deadline_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,
                           clock_timestamp()+(%s*interval '1 second'))
                   ON CONFLICT(wait_id) DO NOTHING RETURNING *""",
                (
                    kwargs["wait_id"],
                    kwargs["run_id"],
                    kwargs["task_id"],
                    task["user_id"],
                    kwargs["event_type"],
                    Jsonb(kwargs["payload_schema"]),
                    kwargs["config_hash"],
                    kwargs["deadline_seconds"],
                ),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM graph_event_waits WHERE wait_id=%s FOR UPDATE",
                    (kwargs["wait_id"],),
                ).fetchone()
            if (
                row is None
                or str(row["task_id"]) != kwargs["task_id"]
                or str(row["config_hash"]) != kwargs["config_hash"]
                or str(row["status"]) != "pending"
            ):
                raise RuntimeError("Graph event wait identity conflict")
            result = {
                "status": "waiting_external",
                "stop_reason": "wait_event",
                "wait_id": kwargs["wait_id"],
                "event_type": kwargs["event_type"],
                "deadline_at": self._iso_value(row["deadline_at"]),
            }
            saved = conn.execute(
                """UPDATE runtime_tasks SET status='waiting_external',result=%s,error=NULL,
                       lease_owner=NULL,lease_expires_at=NULL,finished_at=NULL,
                       updated_at=clock_timestamp()
                   WHERE task_id=%s AND status='running' AND lease_owner=%s
                     AND lease_version=%s RETURNING run_id""",
                (
                    Jsonb(result),
                    kwargs["task_id"],
                    kwargs["worker_id"],
                    kwargs["lease_version"],
                ),
            ).fetchone()
            if saved is None:
                return None
            self._refresh_graph_run_waiting(conn, kwargs["run_id"])
            self._audit(
                conn,
                run_id=kwargs["run_id"],
                task_id=kwargs["task_id"],
                worker_id=kwargs["worker_id"],
                stage="store.graph.event.waiting",
                message="Graph Task suspended for a token-authenticated external event",
                data={
                    "wait_id": kwargs["wait_id"],
                    "event_type": kwargs["event_type"],
                    "deadline_at": result["deadline_at"],
                },
            )
            self._notify(conn, kwargs["run_id"])
            return self._event_wait(row)

    def list_graph_event_waits(
        self, run_id: str, *, expected_user_id: str
    ) -> list[GraphEventWaitRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM graph_event_waits WHERE run_id=%s AND user_id=%s
                   ORDER BY created_at,wait_id""",
                (run_id, expected_user_id),
            ).fetchall()
        return [self._event_wait(row) for row in rows]

    def get_graph_event_wait(
        self, wait_id: str, *, expected_user_id: str | None = None
    ) -> GraphEventWaitRecord | None:
        clauses = ["wait_id=%s"]
        params: list[Any] = [wait_id]
        if expected_user_id is not None:
            clauses.append("user_id=%s")
            params.append(expected_user_id)
        with self._pool.connection() as conn:
            row = conn.execute(
                f"SELECT * FROM graph_event_waits WHERE {' AND '.join(clauses)}", params
            ).fetchone()
        return self._event_wait(row) if row else None

    def issue_graph_event_token(
        self, wait_id: str, *, expected_user_id: str, actor_id: str | None = None
    ) -> tuple[GraphEventWaitRecord, str] | None:
        token = secrets.token_urlsafe(32)
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE graph_event_waits SET token_hash=%s,
                       token_version=token_version+1,token_issued_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE wait_id=%s AND user_id=%s AND status='pending'
                     AND deadline_at>clock_timestamp() RETURNING *""",
                (_token_hash(token), wait_id, expected_user_id),
            ).fetchone()
            if row is None:
                return None
            self._audit(
                conn,
                run_id=str(row["run_id"]),
                task_id=str(row["task_id"]),
                stage="store.graph.event.token_issued",
                message="External event delivery token issued",
                data={
                    "wait_id": wait_id,
                    "token_version": int(row["token_version"]),
                    "actor_id": actor_id or expected_user_id,
                },
            )
        return self._event_wait(row), token

    def receive_graph_event(
        self,
        wait_id: str,
        *,
        token: str,
        event_type: str,
        payload: Any,
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT * FROM graph_event_waits WHERE wait_id=%s FOR UPDATE",
                (wait_id,),
            ).fetchone()
            if (
                row is None
                or not row["token_hash"]
                or not hmac.compare_digest(str(row["token_hash"]), _token_hash(token))
            ):
                return {"status": "not_found"}
            if str(row["status"]) == "received":
                if str(row["event_type"]) != event_type or str(
                    row["payload_hash"] or ""
                ) != _hash_json(payload):
                    return {"status": "idempotency_conflict"}
                return {"status": "received", "duplicate": True, "record": self._event_wait(row)}
            if str(row["status"]) != "pending":
                return {"status": str(row["status"]), "record": self._event_wait(row)}
            if conn.execute(
                "SELECT %s::timestamptz<=clock_timestamp() AS due", (row["deadline_at"],)
            ).fetchone()["due"]:
                expired = self._expire_wait(conn, row)
                self._notify(conn, str(row["run_id"]))
                return {"status": "expired", "record": self._event_wait(expired)}
            if str(row["event_type"]) != event_type:
                return {"status": "event_type_mismatch"}
            schema = dict(row["payload_schema"] or {})
            try:
                validator_for(schema)(schema).validate(payload)
            except JsonSchemaValidationError as exc:
                return {"status": "schema_invalid", "message": exc.message}
            payload_hash = _hash_json(payload)
            received = conn.execute(
                """UPDATE graph_event_waits SET status='received',payload=%s,
                       payload_hash=%s,received_at=clock_timestamp(),
                       received_by=%s,updated_at=clock_timestamp()
                   WHERE wait_id=%s AND status='pending' RETURNING *""",
                (
                    Jsonb(payload),
                    payload_hash,
                    f"event_token:v{int(row['token_version'])}",
                    wait_id,
                ),
            ).fetchone()
            result = {
                "status": "completed",
                "stop_reason": "event_received",
                "wait_id": wait_id,
                "event_type": event_type,
                "structured_output": payload,
                "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                "payload_hash": payload_hash,
            }
            task = conn.execute(
                """UPDATE runtime_tasks SET status='completed',result=%s,error=NULL,
                       lease_owner=NULL,lease_expires_at=NULL,finished_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE task_id=%s AND status='waiting_external'
                     AND result->>'wait_id'=%s RETURNING run_id""",
                (Jsonb(result), row["task_id"], wait_id),
            ).fetchone()
            if task is None:
                raise RuntimeError("Graph event Task is no longer waiting")
            self._queue_completed_dependents(conn, str(row["run_id"]))
            self._refresh_graph_run_waiting(conn, str(row["run_id"]))
            self._audit(
                conn,
                run_id=str(row["run_id"]),
                task_id=str(row["task_id"]),
                stage="store.graph.event.received",
                message="External event accepted and waiting Graph Task completed",
                data={
                    "wait_id": wait_id,
                    "event_type": event_type,
                    "payload_hash": payload_hash,
                },
            )
            self._notify(conn, str(row["run_id"]))
            return {
                "status": "received",
                "duplicate": False,
                "record": self._event_wait(received),
                "task_result": result,
            }

    def expire_due_graph_event_waits(
        self, *, run_id: str | None = None, limit: int = 500
    ) -> list[GraphEventWaitRecord]:
        with self._pool.connection() as conn, conn.transaction():
            rows = conn.execute(
                """SELECT * FROM graph_event_waits
                   WHERE status='pending' AND deadline_at<=clock_timestamp()
                     AND (%s::text IS NULL OR run_id=%s)
                   ORDER BY deadline_at FOR UPDATE SKIP LOCKED LIMIT %s""",
                (run_id, run_id, max(1, min(5000, limit))),
            ).fetchall()
            expired = [self._expire_wait(conn, row) for row in rows]
            for row in expired:
                self._notify(conn, str(row["run_id"]))
        return [self._event_wait(row) for row in expired]

    def _expire_wait(self, conn: Any, row: dict[str, Any]) -> dict[str, Any]:
        expired = conn.execute(
            """UPDATE graph_event_waits SET status='expired',updated_at=clock_timestamp()
               WHERE wait_id=%s AND status='pending' RETURNING *""",
            (row["wait_id"],),
        ).fetchone()
        if expired is None:
            return row
        conn.execute(
            """UPDATE runtime_tasks SET status='failed',
                   error=%s,lease_owner=NULL,lease_expires_at=NULL,
                   finished_at=clock_timestamp(),updated_at=clock_timestamp()
               WHERE task_id=%s AND status='waiting_external'
                 AND result->>'wait_id'=%s""",
            (
                Jsonb({"message": "external event deadline expired"}),
                row["task_id"],
                row["wait_id"],
            ),
        )
        self._refresh_graph_run_waiting(conn, str(row["run_id"]))
        self._audit(
            conn,
            run_id=str(row["run_id"]),
            task_id=str(row["task_id"]),
            stage="store.graph.event.expired",
            message="External event deadline expired",
            level="error",
            data={"wait_id": str(row["wait_id"])},
        )
        return expired

    @staticmethod
    def _queue_completed_dependents(conn: Any, run_id: str) -> None:
        conn.execute(
            """UPDATE runtime_tasks task SET status='queued',updated_at=clock_timestamp()
               WHERE task.run_id=%s AND task.status='blocked' AND NOT EXISTS (
                   SELECT 1 FROM runtime_task_dependencies dependency
                   JOIN runtime_tasks parent
                     ON parent.task_id=dependency.depends_on_task_id
                   WHERE dependency.task_id=task.task_id AND parent.status!='completed')""",
            (run_id,),
        )

    @staticmethod
    def _iso_value(value: Any) -> str:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    def _event_wait(self, row: dict[str, Any]) -> GraphEventWaitRecord:
        return GraphEventWaitRecord(
            wait_id=str(row["wait_id"]),
            run_id=str(row["run_id"]),
            task_id=str(row["task_id"]),
            user_id=str(row["user_id"]),
            event_type=str(row["event_type"]),
            payload_schema=dict(row["payload_schema"] or {}),
            config_hash=str(row["config_hash"]),
            status=str(row["status"]),
            token_version=int(row["token_version"] or 0),
            token_issued_at=(
                self._iso_value(row["token_issued_at"])
                if row["token_issued_at"] is not None
                else None
            ),
            deadline_at=self._iso_value(row["deadline_at"]),
            payload=row["payload"],
            payload_hash=row["payload_hash"],
            received_at=(
                self._iso_value(row["received_at"]) if row["received_at"] is not None else None
            ),
            received_by=row["received_by"],
            created_at=self._iso_value(row["created_at"]),
            updated_at=self._iso_value(row["updated_at"]),
        )
