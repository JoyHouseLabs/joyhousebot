"""PostgreSQL-fenced state machine for durable output verification."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.verification_records import VerificationRecord

_TERMINAL = {"passed", "failed"}


class PostgresVerificationStoreMixin:
    def migrate_verifications(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS verification_records (
            verification_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            turn_id TEXT,
            user_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            verifier_id TEXT NOT NULL,
            verifier_type TEXT NOT NULL,
            verifier_version TEXT NOT NULL DEFAULT '1',
            required BOOLEAN NOT NULL DEFAULT TRUE,
            repairable BOOLEAN NOT NULL DEFAULT TRUE,
            status TEXT NOT NULL DEFAULT 'running',
            policy JSONB NOT NULL DEFAULT '{}'::jsonb,
            input_hash TEXT NOT NULL,
            evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
            error JSONB,
            worker_id TEXT,
            run_lease_version BIGINT NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            finished_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(run_id, task_id, turn_id, attempt, verifier_id)
        );
        CREATE INDEX IF NOT EXISTS ix_verification_records_run_attempt
            ON verification_records(run_id, attempt, verifier_id);
        CREATE INDEX IF NOT EXISTS ix_verification_records_required_status
            ON verification_records(run_id, status, attempt) WHERE required;
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341925,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="verifications",
                version=1,
                ddl=ddl,
                description="fenced schema, artifact, and deterministic verification records",
            )
            upgrade = """
            ALTER TABLE verification_records
                ALTER COLUMN run_lease_version DROP NOT NULL;
            ALTER TABLE verification_records
                ADD COLUMN IF NOT EXISTS task_lease_version BIGINT;
            """
            conn.execute(upgrade)
            self._record_migration(
                conn,
                name="verifications",
                version=2,
                ddl=upgrade,
                description="allow verification records fenced by a Graph Task lease",
            )

    def begin_verification(self, **kwargs: Any) -> VerificationRecord | None:
        """Create or recover one verifier attempt under the current Run lease."""

        with self._pool.connection() as conn, conn.transaction():
            current = conn.execute(
                "SELECT * FROM verification_records WHERE verification_id=%s FOR UPDATE",
                (kwargs["verification_id"],),
            ).fetchone()
            if current is not None:
                self._assert_frozen_verification(current, kwargs)
                if str(current["status"]) in _TERMINAL:
                    return self._verification(current)
            task_lease_version = kwargs.get("task_lease_version")
            run_lease_version = kwargs.get("run_lease_version")
            if task_lease_version is not None and kwargs.get("task_id"):
                owned = conn.execute(
                    """SELECT run.user_id FROM runtime_tasks task
                       JOIN runtime_runs run ON run.run_id=task.run_id
                       WHERE task.task_id=%s AND task.run_id=%s AND task.status='running'
                         AND task.lease_owner=%s AND task.lease_version=%s FOR UPDATE OF task""",
                    (
                        kwargs["task_id"], kwargs["run_id"], kwargs["worker_id"],
                        task_lease_version,
                    ),
                ).fetchone()
            else:
                owned = conn.execute(
                    """SELECT user_id FROM runtime_runs
                       WHERE run_id=%s AND status='running' AND lease_owner=%s
                         AND lease_version=%s FOR UPDATE""",
                    (kwargs["run_id"], kwargs["worker_id"], run_lease_version),
                ).fetchone()
            if owned is None or str(owned["user_id"]) != kwargs["user_id"]:
                return None
            if current is None:
                row = conn.execute(
                    """INSERT INTO verification_records
                           (verification_id,run_id,task_id,turn_id,user_id,attempt,
                            verifier_id,verifier_type,verifier_version,required,repairable,
                            status,policy,input_hash,worker_id,run_lease_version,
                            task_lease_version)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'running',%s,%s,%s,%s,%s)
                       RETURNING *""",
                    (
                        kwargs["verification_id"], kwargs["run_id"], kwargs.get("task_id"),
                        kwargs.get("turn_id"), kwargs["user_id"], int(kwargs["attempt"]),
                        kwargs["verifier_id"], kwargs["verifier_type"],
                        kwargs.get("verifier_version") or "1", bool(kwargs.get("required", True)),
                        bool(kwargs.get("repairable", True)), Jsonb(kwargs.get("policy") or {}),
                        kwargs["input_hash"], kwargs["worker_id"], run_lease_version,
                        task_lease_version,
                    ),
                ).fetchone()
            else:
                row = conn.execute(
                    """UPDATE verification_records SET worker_id=%s,run_lease_version=%s,
                           task_lease_version=%s,
                           status='running',updated_at=clock_timestamp()
                       WHERE verification_id=%s RETURNING *""",
                    (
                        kwargs["worker_id"], run_lease_version, task_lease_version,
                        kwargs["verification_id"],
                    ),
                ).fetchone()
        return self._verification(row) if row else None

    def complete_verification(self, verification_id: str, **kwargs: Any) -> VerificationRecord | None:
        status = str(kwargs["status"])
        if status not in _TERMINAL:
            raise ValueError("verification completion status must be passed or failed")
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT * FROM verification_records WHERE verification_id=%s FOR UPDATE",
                (verification_id,),
            ).fetchone()
            if row is None:
                return None
            if str(row["status"]) in _TERMINAL:
                return self._verification(row)
            task_lease_version = kwargs.get("task_lease_version")
            run_lease_version = kwargs.get("run_lease_version")
            if row["task_lease_version"] is not None:
                owned = conn.execute(
                    """SELECT 1 FROM runtime_tasks WHERE task_id=%s AND status='running'
                       AND lease_owner=%s AND lease_version=%s""",
                    (row["task_id"], kwargs["worker_id"], task_lease_version),
                ).fetchone()
                lease_matches = int(row["task_lease_version"]) == int(
                    task_lease_version or -1
                )
            else:
                owned = conn.execute(
                    """SELECT 1 FROM runtime_runs WHERE run_id=%s AND status='running'
                       AND lease_owner=%s AND lease_version=%s""",
                    (row["run_id"], kwargs["worker_id"], run_lease_version),
                ).fetchone()
                lease_matches = int(row["run_lease_version"]) == int(
                    run_lease_version or -1
                )
            if owned is None or row["worker_id"] != kwargs["worker_id"] or not lease_matches:
                return None
            saved = conn.execute(
                """UPDATE verification_records SET status=%s,evidence=%s,error=%s,
                       finished_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE verification_id=%s RETURNING *""",
                (
                    status, Jsonb(kwargs.get("evidence") or {}),
                    Jsonb(kwargs["error"]) if kwargs.get("error") else None,
                    verification_id,
                ),
            ).fetchone()
        return self._verification(saved) if saved else None

    def list_verification_records(
        self, run_id: str, *, expected_user_id: str | None = None
    ) -> list[VerificationRecord]:
        clause = " AND user_id=%s" if expected_user_id is not None else ""
        params = (run_id, expected_user_id) if expected_user_id is not None else (run_id,)
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM verification_records WHERE run_id=%s""" + clause
                + " ORDER BY attempt,created_at,verifier_id",
                params,
            ).fetchall()
        return [self._verification(row) for row in rows]

    @staticmethod
    def _assert_frozen_verification(row: dict[str, Any], value: dict[str, Any]) -> None:
        frozen = (
            str(row["run_id"]) == value["run_id"]
            and row["task_id"] == value.get("task_id")
            and row["turn_id"] == value.get("turn_id")
            and str(row["user_id"]) == value["user_id"]
            and int(row["attempt"]) == int(value["attempt"])
            and str(row["verifier_id"]) == value["verifier_id"]
            and str(row["verifier_type"]) == value["verifier_type"]
            and str(row["verifier_version"]) == str(value.get("verifier_version") or "1")
            and bool(row["required"]) == bool(value.get("required", True))
            and bool(row["repairable"]) == bool(value.get("repairable", True))
            and dict(row["policy"]) == dict(value.get("policy") or {})
            and str(row["input_hash"]) == value["input_hash"]
        )
        if not frozen:
            raise RuntimeError(f"verification identity conflict: {row['verification_id']}")

    @staticmethod
    def _verification(row: dict[str, Any]) -> VerificationRecord:
        from joyhousebot.storage.postgres_store import _iso, _json

        return VerificationRecord(
            verification_id=str(row["verification_id"]), run_id=str(row["run_id"]),
            task_id=row["task_id"], turn_id=row["turn_id"], user_id=str(row["user_id"]),
            attempt=int(row["attempt"]), verifier_id=str(row["verifier_id"]),
            verifier_type=str(row["verifier_type"]), verifier_version=str(row["verifier_version"]),
            required=bool(row["required"]), repairable=bool(row["repairable"]),
            status=str(row["status"]), policy=dict(_json(row["policy"], {})),
            input_hash=str(row["input_hash"]), evidence=dict(_json(row["evidence"], {})),
            error=_json(row["error"]), worker_id=row["worker_id"],
            run_lease_version=(
                int(row["run_lease_version"])
                if row["run_lease_version"] is not None
                else None
            ),
            task_lease_version=(
                int(row["task_lease_version"])
                if row["task_lease_version"] is not None
                else None
            ),
            started_at=_iso(row["started_at"]) or "", finished_at=_iso(row["finished_at"]),
            created_at=_iso(row["created_at"]) or "", updated_at=_iso(row["updated_at"]) or "",
        )
