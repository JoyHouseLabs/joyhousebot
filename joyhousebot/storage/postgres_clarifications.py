"""PostgreSQL persistence for durable clarification state."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from joyhousebot.storage.platform_records import InputRequestRecord, RunScenarioStateRecord


class PostgresClarificationStoreMixin:
    def migrate_clarifications(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS run_scenario_states (
            run_id TEXT PRIMARY KEY REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,scenario_id TEXT NOT NULL,scenario_version INTEGER NOT NULL,
            status TEXT NOT NULL,collected_inputs JSONB NOT NULL DEFAULT '{}'::jsonb,
            missing_inputs JSONB NOT NULL DEFAULT '[]'::jsonb,current_node_id TEXT,
            routing_decision JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_run_scenario_user_updated
            ON run_scenario_states(user_id,updated_at DESC);
        CREATE TABLE IF NOT EXISTS run_input_requests (
            input_request_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,scenario_id TEXT NOT NULL,scenario_version INTEGER NOT NULL,
            node_id TEXT NOT NULL,status TEXT NOT NULL,question TEXT NOT NULL,
            fields JSONB NOT NULL DEFAULT '[]'::jsonb,
            presentation JSONB NOT NULL DEFAULT '{}'::jsonb,
            source TEXT NOT NULL DEFAULT 'scenario',expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),resolved_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS ix_run_input_pending
            ON run_input_requests(user_id,run_id,created_at) WHERE status='pending';
        CREATE UNIQUE INDEX IF NOT EXISTS uq_run_input_pending_node
            ON run_input_requests(run_id,node_id) WHERE status='pending';
        CREATE TABLE IF NOT EXISTS run_input_answers (
            input_request_id TEXT NOT NULL REFERENCES run_input_requests(input_request_id)
                ON DELETE CASCADE,
            field_name TEXT NOT NULL,value JSONB NOT NULL,source TEXT NOT NULL,
            answered_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(input_request_id,field_name)
        );
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341910,))
            conn.execute(ddl)
            conn.execute(
                "ALTER TABLE run_input_requests ADD COLUMN IF NOT EXISTS "
                "presentation JSONB NOT NULL DEFAULT '{}'::jsonb"
            )
            conn.execute(
                "ALTER TABLE run_input_requests ADD COLUMN IF NOT EXISTS "
                "source TEXT NOT NULL DEFAULT 'scenario'"
            )

    def save_run_scenario_state(
        self, *, run_id: str, user_id: str, scenario_id: str, scenario_version: int,
        status: str, collected_inputs: dict[str, Any], missing_inputs: list[str],
        current_node_id: str | None, routing_decision: dict[str, Any],
    ) -> RunScenarioStateRecord:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO run_scenario_states
                       (run_id,user_id,scenario_id,scenario_version,status,collected_inputs,
                        missing_inputs,current_node_id,routing_decision)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(run_id) DO UPDATE SET
                       status=excluded.status,collected_inputs=excluded.collected_inputs,
                       missing_inputs=excluded.missing_inputs,
                       current_node_id=excluded.current_node_id,
                       routing_decision=excluded.routing_decision,updated_at=clock_timestamp()
                   RETURNING *""",
                (run_id, user_id, scenario_id, scenario_version, status,
                 Jsonb(collected_inputs), Jsonb(missing_inputs), current_node_id,
                 Jsonb(routing_decision)),
            ).fetchone()
        return self._scenario_state(row)

    def get_run_scenario_state(
        self, run_id: str, *, expected_user_id: str
    ) -> RunScenarioStateRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM run_scenario_states WHERE run_id=%s AND user_id=%s",
                (run_id, expected_user_id),
            ).fetchone()
        return self._scenario_state(row) if row else None

    def create_input_request(
        self, *, input_request_id: str, run_id: str, user_id: str,
        scenario_id: str, scenario_version: int, node_id: str, question: str,
        fields: list[dict[str, Any]], presentation: dict[str, Any] | None = None,
        source: str = "scenario", expires_at: str | None = None,
    ) -> InputRequestRecord:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO run_input_requests
                       (input_request_id,run_id,user_id,scenario_id,scenario_version,node_id,
                        status,question,fields,presentation,source,expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s)
                   ON CONFLICT(run_id,node_id) WHERE status='pending' DO NOTHING RETURNING *""",
                (input_request_id, run_id, user_id, scenario_id, scenario_version, node_id,
                 question, Jsonb(fields), Jsonb(presentation or {}), source, expires_at),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """SELECT * FROM run_input_requests
                       WHERE run_id=%s AND node_id=%s AND status='pending'""",
                    (run_id, node_id),
                ).fetchone()
        assert row is not None
        return self._input_request(row)

    def list_pending_input_requests(
        self, run_id: str, *, expected_user_id: str
    ) -> list[InputRequestRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM run_input_requests WHERE run_id=%s AND user_id=%s
                   AND status='pending' ORDER BY created_at""",
                (run_id, expected_user_id),
            ).fetchall()
        return [self._input_request(row) for row in rows]

    def get_input_request(
        self, input_request_id: str, *, expected_user_id: str
    ) -> InputRequestRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM run_input_requests
                   WHERE input_request_id=%s AND user_id=%s""",
                (input_request_id, expected_user_id),
            ).fetchone()
        return self._input_request(row) if row else None

    def resolve_input_request(
        self, *, input_request_id: str, run_id: str, user_id: str,
        answers: dict[str, Any], collected_inputs: dict[str, Any],
        missing_inputs: list[str], current_node_id: str | None,
        scenario_status: str, run_status: str,
    ) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            request = conn.execute(
                """SELECT status FROM run_input_requests WHERE input_request_id=%s
                   AND run_id=%s AND user_id=%s FOR UPDATE""",
                (input_request_id, run_id, user_id),
            ).fetchone()
            if request is None or request["status"] != "pending":
                return False
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO run_input_answers
                           (input_request_id,field_name,value,source)
                       VALUES (%s,%s,%s,'user')""",
                    [(input_request_id, name, Jsonb(value)) for name, value in answers.items()],
                )
            conn.execute(
                """UPDATE run_input_requests SET status='resolved',resolved_at=clock_timestamp()
                   WHERE input_request_id=%s""", (input_request_id,),
            )
            conn.execute(
                """UPDATE run_scenario_states SET status=%s,collected_inputs=%s,
                       missing_inputs=%s,current_node_id=%s,updated_at=clock_timestamp()
                   WHERE run_id=%s AND user_id=%s""",
                (scenario_status, Jsonb(collected_inputs), Jsonb(missing_inputs),
                 current_node_id, run_id, user_id),
            )
            updated = conn.execute(
                """UPDATE runtime_runs SET status=%s,waiting_on=%s,
                       updated_at=clock_timestamp() WHERE run_id=%s AND user_id=%s
                       AND status='waiting_input' RETURNING run_id""",
                (run_status, current_node_id, run_id, user_id),
            ).fetchone()
        return updated is not None

    def resolve_dynamic_input_request(
        self,
        *,
        input_request_id: str,
        run_id: str,
        user_id: str,
        answers: dict[str, Any],
    ) -> bool:
        """Resolve a coordinator-created request and atomically requeue its Run."""
        with self._pool.connection() as conn, conn.transaction():
            request = conn.execute(
                """SELECT status,source FROM run_input_requests WHERE input_request_id=%s
                   AND run_id=%s AND user_id=%s FOR UPDATE""",
                (input_request_id, run_id, user_id),
            ).fetchone()
            if request is None or request["status"] != "pending" or request["source"] != "agent":
                return False
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO run_input_answers
                           (input_request_id,field_name,value,source)
                       VALUES (%s,%s,%s,'user')""",
                    [(input_request_id, name, Jsonb(value)) for name, value in answers.items()],
                )
            conn.execute(
                """UPDATE run_input_requests SET status='resolved',resolved_at=clock_timestamp()
                   WHERE input_request_id=%s""",
                (input_request_id,),
            )
            updated = conn.execute(
                """UPDATE runtime_runs SET status='queued',waiting_on=NULL,
                       options=jsonb_set(
                           options,
                           '{metadata,dynamic_inputs}',
                           COALESCE(options #> '{metadata,dynamic_inputs}', '{}'::jsonb) || %s::jsonb,
                           true
                       ),updated_at=clock_timestamp()
                   WHERE run_id=%s AND user_id=%s AND status='waiting_input'
                   RETURNING run_id""",
                (Jsonb(answers), run_id, user_id),
            ).fetchone()
        return updated is not None

    @staticmethod
    def _scenario_state(row: dict[str, Any]) -> RunScenarioStateRecord:
        from joyhousebot.storage.postgres_store import _iso

        return RunScenarioStateRecord(
            run_id=str(row["run_id"]), user_id=str(row["user_id"]),
            scenario_id=str(row["scenario_id"]), scenario_version=int(row["scenario_version"]),
            status=str(row["status"]), collected_inputs=dict(row["collected_inputs"]),
            missing_inputs=list(row["missing_inputs"]), current_node_id=row["current_node_id"],
            routing_decision=dict(row["routing_decision"]),
            created_at=_iso(row["created_at"]) or "", updated_at=_iso(row["updated_at"]) or "",
        )

    @staticmethod
    def _input_request(row: dict[str, Any]) -> InputRequestRecord:
        from joyhousebot.storage.postgres_store import _iso

        return InputRequestRecord(
            input_request_id=str(row["input_request_id"]), run_id=str(row["run_id"]),
            user_id=str(row["user_id"]), scenario_id=str(row["scenario_id"]),
            scenario_version=int(row["scenario_version"]), node_id=str(row["node_id"]),
            status=str(row["status"]), question=str(row["question"]),
            fields=list(row["fields"]), presentation=dict(row["presentation"] or {}),
            source=str(row["source"]), expires_at=_iso(row["expires_at"]),
            created_at=_iso(row["created_at"]) or "", resolved_at=_iso(row["resolved_at"]),
        )
