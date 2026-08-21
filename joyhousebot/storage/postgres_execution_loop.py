"""PostgreSQL persistence for durable Agent turns, actions, and observations."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.execution_records import (
    ActionIntentRecord,
    ActionObservationRecord,
    RuntimeTurnRecord,
)
from joyhousebot.storage.json_codec import Jsonb


class PostgresExecutionLoopStoreMixin:
    def migrate_execution_loop(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS runtime_turns (
            turn_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            turn_index INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned',
            model TEXT,
            request_hash TEXT NOT NULL,
            response JSONB,
            usage JSONB NOT NULL DEFAULT '{}'::jsonb,
            stop_reason TEXT,
            error JSONB,
            worker_id TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            finished_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(run_id, task_id, turn_index)
        );
        -- The legacy root-turn index intentionally is not recreated here.
        -- Version 2 keys root turns by scope; recreating the v1 index on every
        -- startup rejects valid coordinator turns that share a turn_index.
        CREATE INDEX IF NOT EXISTS ix_runtime_turns_run_index
            ON runtime_turns(run_id, turn_index);
        CREATE INDEX IF NOT EXISTS ix_runtime_turns_active
            ON runtime_turns(status, updated_at)
            WHERE status NOT IN ('completed', 'failed', 'exhausted');

        CREATE TABLE IF NOT EXISTS action_intents (
            action_id TEXT PRIMARY KEY,
            turn_id TEXT NOT NULL REFERENCES runtime_turns(turn_id) ON DELETE CASCADE,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            turn_index INTEGER NOT NULL,
            action_index INTEGER NOT NULL,
            capability_ref JSONB NOT NULL,
            input JSONB NOT NULL DEFAULT '{}'::jsonb,
            input_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            side_effect TEXT NOT NULL DEFAULT 'unknown',
            idempotent BOOLEAN NOT NULL DEFAULT FALSE,
            retryable BOOLEAN NOT NULL DEFAULT FALSE,
            risk TEXT NOT NULL DEFAULT 'medium',
            approval_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            idempotency_key TEXT NOT NULL,
            invocation_id TEXT NOT NULL,
            worker_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(turn_id, action_index),
            UNIQUE(run_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS ix_action_intents_run_created
            ON action_intents(run_id, created_at);
        CREATE INDEX IF NOT EXISTS ix_action_intents_active
            ON action_intents(status, updated_at)
            WHERE status IN ('proposed', 'approval_pending', 'invoking', 'waiting_external');

        CREATE TABLE IF NOT EXISTS action_observations (
            observation_id TEXT PRIMARY KEY,
            action_id TEXT NOT NULL UNIQUE REFERENCES action_intents(action_id) ON DELETE CASCADE,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            invocation_id TEXT NOT NULL,
            status TEXT NOT NULL,
            result JSONB,
            error JSONB,
            operation JSONB,
            reconciliation_status TEXT NOT NULL DEFAULT 'confirmed',
            observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_action_observations_run_observed
            ON action_observations(run_id, observed_at);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341922,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="execution_loop",
                version=1,
                ddl=ddl,
                description="durable Agent turns, action intents, and observations",
            )
            upgrade = """
            ALTER TABLE runtime_turns
                ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'execution';
            DROP INDEX IF EXISTS uq_runtime_turns_run_root_index;
            CREATE UNIQUE INDEX IF NOT EXISTS uq_runtime_turns_run_root_scope_index
                ON runtime_turns(run_id, scope, turn_index) WHERE task_id IS NULL;
            """
            conn.execute(upgrade)
            self._record_migration(
                conn,
                name="execution_loop",
                version=2,
                ddl=upgrade,
                description="isolate coordinator planning turns from root execution turns",
            )

    def create_runtime_turn(
        self,
        *,
        turn_id: str,
        run_id: str,
        task_id: str | None,
        turn_index: int,
        model: str | None,
        request_hash: str,
        worker_id: str | None,
        scope: str = "execution",
    ) -> tuple[RuntimeTurnRecord, bool]:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO runtime_turns
                       (turn_id,run_id,task_id,scope,turn_index,status,model,request_hash,worker_id)
                   VALUES (%s,%s,%s,%s,%s,'planned',%s,%s,%s)
                   ON CONFLICT(turn_id) DO NOTHING
                   RETURNING *,TRUE AS created""",
                (
                    turn_id,
                    run_id,
                    task_id,
                    scope,
                    turn_index,
                    model,
                    request_hash,
                    worker_id,
                ),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT *,FALSE AS created FROM runtime_turns WHERE turn_id=%s",
                    (turn_id,),
                ).fetchone()
        assert row is not None
        record = self._runtime_turn(row)
        if (
            record.run_id != run_id
            or record.task_id != task_id
            or record.scope != scope
            or record.turn_index != int(turn_index)
        ):
            raise RuntimeError(f"durable turn identity conflict: {turn_id}")
        return record, bool(row["created"])

    def get_runtime_turn(self, turn_id: str) -> RuntimeTurnRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_turns WHERE turn_id=%s", (turn_id,)
            ).fetchone()
        return self._runtime_turn(row) if row else None

    def list_runtime_turns(self, run_id: str) -> list[RuntimeTurnRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime_turns WHERE run_id=%s ORDER BY turn_index,started_at",
                (run_id,),
            ).fetchall()
        return [self._runtime_turn(row) for row in rows]

    def record_runtime_turn_response(
        self,
        turn_id: str,
        *,
        model: str,
        response: dict[str, Any],
        usage: dict[str, Any],
        status: str,
    ) -> bool:
        encoded_response = Jsonb(response)
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE runtime_turns
                   SET model=%s,response=COALESCE(response,%s),usage=%s,status=%s,
                       updated_at=clock_timestamp()
                   WHERE turn_id=%s AND (response IS NULL OR response=%s)
                   RETURNING turn_id""",
                (
                    model,
                    encoded_response,
                    Jsonb(usage),
                    status,
                    turn_id,
                    encoded_response,
                ),
            ).fetchone()
        return row is not None

    def finish_runtime_turn(
        self,
        turn_id: str,
        *,
        status: str,
        stop_reason: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> bool:
        terminal = status in {"completed", "failed", "exhausted"}
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE runtime_turns SET status=%s,stop_reason=%s,error=%s,
                       finished_at=CASE WHEN %s THEN clock_timestamp() ELSE finished_at END,
                       updated_at=clock_timestamp()
                   WHERE turn_id=%s RETURNING turn_id""",
                (
                    status,
                    stop_reason,
                    Jsonb(error) if error is not None else None,
                    terminal,
                    turn_id,
                ),
            ).fetchone()
        return row is not None

    def create_action_intent(self, **kwargs: Any) -> tuple[ActionIntentRecord, bool]:
        values = (
            kwargs["action_id"],
            kwargs["turn_id"],
            kwargs["run_id"],
            kwargs.get("task_id"),
            int(kwargs["turn_index"]),
            int(kwargs["action_index"]),
            Jsonb(kwargs["capability_ref"]),
            Jsonb(kwargs["input"]),
            kwargs["input_hash"],
            kwargs.get("side_effect") or "unknown",
            bool(kwargs.get("idempotent")),
            bool(kwargs.get("retryable")),
            kwargs.get("risk") or "medium",
            Jsonb(kwargs.get("approval_policy") or {}),
            kwargs["idempotency_key"],
            kwargs["invocation_id"],
        )
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO action_intents
                       (action_id,turn_id,run_id,task_id,turn_index,action_index,
                        capability_ref,input,input_hash,side_effect,idempotent,retryable,
                        risk,approval_policy,idempotency_key,invocation_id,status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'proposed')
                   ON CONFLICT(action_id) DO NOTHING
                   RETURNING *,TRUE AS created""",
                values,
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT *,FALSE AS created FROM action_intents WHERE action_id=%s",
                    (kwargs["action_id"],),
                ).fetchone()
        assert row is not None
        record = self._action_intent(row)
        if (
            record.turn_id != kwargs["turn_id"]
            or record.action_index != int(kwargs["action_index"])
            or record.input_hash != kwargs["input_hash"]
            or record.capability_ref != kwargs["capability_ref"]
        ):
            raise RuntimeError(f"durable action identity conflict: {record.action_id}")
        return record, bool(row["created"])

    def get_action_intent(self, action_id: str) -> ActionIntentRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM action_intents WHERE action_id=%s", (action_id,)
            ).fetchone()
        return self._action_intent(row) if row else None

    def claim_action_intent(self, action_id: str, *, worker_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE action_intents SET status='invoking',worker_id=%s,
                       updated_at=clock_timestamp()
                   WHERE action_id=%s AND status='proposed'
                   RETURNING action_id""",
                (worker_id, action_id),
            ).fetchone()
        return row is not None

    def record_action_observation(
        self,
        *,
        action_id: str,
        run_id: str,
        invocation_id: str,
        status: str,
        result: dict[str, Any] | None,
        error: dict[str, Any] | None,
        operation: dict[str, Any] | None,
        reconciliation_status: str = "confirmed",
    ) -> tuple[ActionObservationRecord, bool]:
        observation_id = f"obs_{action_id}"
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO action_observations
                       (observation_id,action_id,run_id,invocation_id,status,result,error,
                        operation,reconciliation_status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(action_id) DO NOTHING
                   RETURNING *,TRUE AS created""",
                (
                    observation_id,
                    action_id,
                    run_id,
                    invocation_id,
                    status,
                    Jsonb(result) if result is not None else None,
                    Jsonb(error) if error is not None else None,
                    Jsonb(operation) if operation is not None else None,
                    reconciliation_status,
                ),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """SELECT *,FALSE AS created FROM action_observations
                       WHERE action_id=%s""",
                    (action_id,),
                ).fetchone()
            action_status = (
                "waiting_external"
                if reconciliation_status != "confirmed" or status == "accepted"
                else "observed"
            )
            conn.execute(
                """UPDATE action_intents SET status=%s,updated_at=clock_timestamp()
                   WHERE action_id=%s""",
                (action_status, action_id),
            )
        assert row is not None
        return self._action_observation(row), bool(row["created"])

    def get_action_observation(self, action_id: str) -> ActionObservationRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM action_observations WHERE action_id=%s", (action_id,)
            ).fetchone()
        return self._action_observation(row) if row else None

    def list_action_intents(self, run_id: str) -> list[ActionIntentRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM action_intents WHERE run_id=%s
                   ORDER BY turn_index,action_index""",
                (run_id,),
            ).fetchall()
        return [self._action_intent(row) for row in rows]

    @staticmethod
    def _runtime_turn(row: dict[str, Any]) -> RuntimeTurnRecord:
        from joyhousebot.storage.postgres_store import _iso, _json

        return RuntimeTurnRecord(
            turn_id=str(row["turn_id"]),
            run_id=str(row["run_id"]),
            task_id=row["task_id"],
            scope=str(row.get("scope") or "execution"),
            turn_index=int(row["turn_index"]),
            status=str(row["status"]),
            model=row["model"],
            request_hash=str(row["request_hash"]),
            response=_json(row["response"]),
            usage=dict(_json(row["usage"], {})),
            stop_reason=row["stop_reason"],
            error=_json(row["error"]),
            worker_id=row["worker_id"],
            started_at=_iso(row["started_at"]) or "",
            finished_at=_iso(row["finished_at"]),
            updated_at=_iso(row["updated_at"]) or "",
        )

    @staticmethod
    def _action_intent(row: dict[str, Any]) -> ActionIntentRecord:
        from joyhousebot.storage.postgres_store import _iso, _json

        return ActionIntentRecord(
            action_id=str(row["action_id"]),
            turn_id=str(row["turn_id"]),
            run_id=str(row["run_id"]),
            task_id=row["task_id"],
            turn_index=int(row["turn_index"]),
            action_index=int(row["action_index"]),
            capability_ref=dict(_json(row["capability_ref"], {})),
            input=dict(_json(row["input"], {})),
            input_hash=str(row["input_hash"]),
            status=str(row["status"]),
            side_effect=str(row["side_effect"]),
            idempotent=bool(row["idempotent"]),
            retryable=bool(row["retryable"]),
            risk=str(row["risk"]),
            approval_policy=dict(_json(row["approval_policy"], {})),
            idempotency_key=str(row["idempotency_key"]),
            invocation_id=str(row["invocation_id"]),
            worker_id=row["worker_id"],
            created_at=_iso(row["created_at"]) or "",
            updated_at=_iso(row["updated_at"]) or "",
        )

    @staticmethod
    def _action_observation(row: dict[str, Any]) -> ActionObservationRecord:
        from joyhousebot.storage.postgres_store import _iso, _json

        return ActionObservationRecord(
            observation_id=str(row["observation_id"]),
            action_id=str(row["action_id"]),
            run_id=str(row["run_id"]),
            invocation_id=str(row["invocation_id"]),
            status=str(row["status"]),
            result=_json(row["result"]),
            error=_json(row["error"]),
            operation=_json(row["operation"]),
            reconciliation_status=str(row["reconciliation_status"]),
            observed_at=_iso(row["observed_at"]) or "",
        )
