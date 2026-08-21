"""Append-only PostgreSQL ledger for structured loop decisions."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.decision_records import LoopDecisionRecord
from joyhousebot.storage.json_codec import Jsonb

_DECISIONS = {"continue", "finish", "repair", "replan", "escalate"}


class PostgresLoopDecisionStoreMixin:
    def migrate_loop_decisions(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS loop_decisions (
            decision_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            scope TEXT NOT NULL,
            decision_index INTEGER NOT NULL,
            attempt INTEGER NOT NULL,
            decision TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            summary TEXT NOT NULL,
            input_hash TEXT,
            output_hash TEXT,
            max_replans INTEGER,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            worker_id TEXT,
            run_lease_version BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_loop_decisions_root_index
            ON loop_decisions(run_id, scope, decision_index) WHERE task_id IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_loop_decisions_task_index
            ON loop_decisions(run_id, task_id, scope, decision_index)
            WHERE task_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS ix_loop_decisions_run_created
            ON loop_decisions(run_id, created_at, decision_index);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341927,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="loop_decisions",
                version=1,
                ddl=ddl,
                description="append-only structured repair, replan, and terminal decisions",
            )

    def record_loop_decision(self, **kwargs: Any) -> LoopDecisionRecord | None:
        decision = str(kwargs["decision"])
        if decision not in _DECISIONS:
            raise ValueError(f"unsupported loop decision: {decision}")
        with self._pool.connection() as conn, conn.transaction():
            existing = conn.execute(
                "SELECT * FROM loop_decisions WHERE decision_id=%s FOR UPDATE",
                (kwargs["decision_id"],),
            ).fetchone()
            if existing is not None:
                self._assert_frozen_decision(existing, kwargs)
                return self._loop_decision(existing)
            owned = conn.execute(
                """SELECT 1 FROM runtime_runs
                   WHERE run_id=%s AND status='running' AND lease_owner=%s
                     AND lease_version=%s FOR UPDATE""",
                (
                    kwargs["run_id"],
                    kwargs["worker_id"],
                    kwargs["run_lease_version"],
                ),
            ).fetchone()
            if owned is None:
                return None
            row = conn.execute(
                """INSERT INTO loop_decisions
                       (decision_id,run_id,task_id,scope,decision_index,attempt,decision,
                        reason_code,summary,input_hash,output_hash,max_replans,details,
                        worker_id,run_lease_version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *""",
                (
                    kwargs["decision_id"],
                    kwargs["run_id"],
                    kwargs.get("task_id"),
                    kwargs["scope"],
                    int(kwargs["decision_index"]),
                    int(kwargs["attempt"]),
                    decision,
                    kwargs["reason_code"],
                    kwargs["summary"],
                    kwargs.get("input_hash"),
                    kwargs.get("output_hash"),
                    kwargs.get("max_replans"),
                    Jsonb(kwargs.get("details") or {}),
                    kwargs["worker_id"],
                    kwargs["run_lease_version"],
                ),
            ).fetchone()
        return self._loop_decision(row) if row else None

    def list_loop_decisions(
        self,
        run_id: str,
        *,
        expected_user_id: str | None = None,
        scope: str | None = None,
    ) -> list[LoopDecisionRecord]:
        clauses = ["decision.run_id=%s"]
        params: list[Any] = [run_id]
        if expected_user_id is not None:
            clauses.append("run.user_id=%s")
            params.append(expected_user_id)
        if scope is not None:
            clauses.append("decision.scope=%s")
            params.append(scope)
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT decision.* FROM loop_decisions decision
                   JOIN runtime_runs run ON run.run_id=decision.run_id
                   WHERE """
                + " AND ".join(clauses)
                + " ORDER BY decision.created_at,decision.decision_index",
                tuple(params),
            ).fetchall()
        return [self._loop_decision(row) for row in rows]

    @staticmethod
    def _assert_frozen_decision(row: dict[str, Any], value: dict[str, Any]) -> None:
        frozen = (
            str(row["run_id"]) == value["run_id"]
            and row["task_id"] == value.get("task_id")
            and str(row["scope"]) == value["scope"]
            and int(row["decision_index"]) == int(value["decision_index"])
            and int(row["attempt"]) == int(value["attempt"])
            and str(row["decision"]) == value["decision"]
            and str(row["reason_code"]) == value["reason_code"]
            and str(row["summary"]) == value["summary"]
            and row["input_hash"] == value.get("input_hash")
            and row["output_hash"] == value.get("output_hash")
            and row["max_replans"] == value.get("max_replans")
            and dict(row["details"]) == dict(value.get("details") or {})
        )
        if not frozen:
            raise RuntimeError(f"loop decision identity conflict: {row['decision_id']}")

    @staticmethod
    def _loop_decision(row: dict[str, Any]) -> LoopDecisionRecord:
        from joyhousebot.storage.postgres_store import _iso, _json

        return LoopDecisionRecord(
            decision_id=str(row["decision_id"]),
            run_id=str(row["run_id"]),
            task_id=row["task_id"],
            scope=str(row["scope"]),
            decision_index=int(row["decision_index"]),
            attempt=int(row["attempt"]),
            decision=str(row["decision"]),
            reason_code=str(row["reason_code"]),
            summary=str(row["summary"]),
            input_hash=row["input_hash"],
            output_hash=row["output_hash"],
            max_replans=(int(row["max_replans"]) if row["max_replans"] is not None else None),
            details=dict(_json(row["details"], {})),
            worker_id=row["worker_id"],
            run_lease_version=(
                int(row["run_lease_version"])
                if row["run_lease_version"] is not None
                else None
            ),
            created_at=_iso(row["created_at"]) or "",
        )
