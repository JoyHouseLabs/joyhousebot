"""PostgreSQL evaluation evidence and release-gate state machines."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.postgres_eval_execution import (
    PostgresEvalExecutionStoreMixin,
)
from joyhousebot.storage.postgres_eval_gates import PostgresEvalGateStoreMixin


class PostgresEvalStoreMixin(
    PostgresEvalExecutionStoreMixin, PostgresEvalGateStoreMixin
):
    def migrate_evals(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS eval_suites (
            suite_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            target_types JSONB NOT NULL,
            thresholds JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (suite_id, version),
            CHECK (status IN ('draft','active','retired'))
        );
        CREATE TABLE IF NOT EXISTS eval_cases (
            suite_id TEXT NOT NULL,
            suite_version INTEGER NOT NULL,
            case_id TEXT NOT NULL,
            name TEXT NOT NULL,
            input JSONB NOT NULL DEFAULT '{}'::jsonb,
            expected JSONB,
            scorers JSONB NOT NULL,
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            min_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
            position INTEGER NOT NULL,
            PRIMARY KEY (suite_id, suite_version, case_id),
            FOREIGN KEY (suite_id, suite_version)
                REFERENCES eval_suites(suite_id, version) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS eval_runs (
            eval_run_id TEXT PRIMARY KEY,
            suite_id TEXT NOT NULL,
            suite_version INTEGER NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            target_revision_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            request_hash TEXT NOT NULL UNIQUE,
            metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
            error JSONB,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            finished_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY (suite_id, suite_version)
                REFERENCES eval_suites(suite_id, version),
            CHECK (status IN ('running','passed','failed'))
        );
        CREATE INDEX IF NOT EXISTS ix_eval_runs_target
            ON eval_runs(target_type,target_id,target_revision_id,finished_at DESC);
        CREATE TABLE IF NOT EXISTS eval_case_results (
            eval_run_id TEXT NOT NULL REFERENCES eval_runs(eval_run_id) ON DELETE CASCADE,
            case_id TEXT NOT NULL,
            status TEXT NOT NULL,
            score DOUBLE PRECISION NOT NULL,
            output JSONB,
            scorer_results JSONB NOT NULL,
            metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
            error JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (eval_run_id, case_id),
            CHECK (status IN ('passed','failed'))
        );
        CREATE TABLE IF NOT EXISTS release_gate_policies (
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            target_revision_id TEXT NOT NULL,
            required BOOLEAN NOT NULL DEFAULT TRUE,
            requirements JSONB NOT NULL,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (target_type,target_id,target_revision_id)
        );
        CREATE TABLE IF NOT EXISTS release_gate_decisions (
            decision_id TEXT PRIMARY KEY,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            target_revision_id TEXT NOT NULL,
            purpose TEXT NOT NULL,
            passed BOOLEAN NOT NULL,
            evidence JSONB NOT NULL,
            actor_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_release_gate_decisions_target
            ON release_gate_decisions(
                target_type,target_id,target_revision_id,created_at DESC
            );
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341934,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="evals",
                version=1,
                ddl=ddl,
                description="evaluation suites, scored evidence, and release gates",
            )
            jobs_ddl = """
            CREATE TABLE IF NOT EXISTS eval_execution_jobs (
                eval_run_id TEXT PRIMARY KEY REFERENCES eval_runs(eval_run_id)
                    ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'queued',
                configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
                requested_by TEXT NOT NULL,
                schedule_policy_id TEXT,
                attempt INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                lease_owner TEXT,
                lease_version BIGINT NOT NULL DEFAULT 0,
                lease_expires_at TIMESTAMPTZ,
                error JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                CHECK (status IN ('queued','running','completed','failed','cancelled'))
            );
            CREATE INDEX IF NOT EXISTS ix_eval_execution_jobs_claim
                ON eval_execution_jobs(status,available_at,created_at)
                WHERE status IN ('queued','running');
            CREATE TABLE IF NOT EXISTS eval_schedule_policies (
                policy_id TEXT PRIMARY KEY,
                suite_id TEXT NOT NULL,
                suite_version INTEGER NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                target_revision_id TEXT NOT NULL,
                cadence_seconds INTEGER NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                execution_configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
                next_run_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                last_eval_run_id TEXT REFERENCES eval_runs(eval_run_id),
                created_by TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                FOREIGN KEY (suite_id,suite_version)
                    REFERENCES eval_suites(suite_id,version),
                CHECK (cadence_seconds BETWEEN 60 AND 31536000)
            );
            CREATE INDEX IF NOT EXISTS ix_eval_schedule_policies_due
                ON eval_schedule_policies(next_run_at,policy_id) WHERE enabled;
            """
            conn.execute(jobs_ddl)
            self._record_migration(
                conn,
                name="evals",
                version=2,
                ddl=jobs_ddl,
                description="leased resumable Eval jobs and recurring quality policies",
            )

    def save_eval_suite(
        self, *, suite: dict[str, Any], cases: list[dict[str, Any]]
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            existing = conn.execute(
                "SELECT status FROM eval_suites WHERE suite_id=%s AND version=%s FOR UPDATE",
                (suite["suite_id"], suite["version"]),
            ).fetchone()
            if existing is not None and str(existing["status"]) == "active":
                raise ValueError("active evaluation suites are immutable")
            conn.execute(
                """INSERT INTO eval_suites
                       (suite_id,version,name,description,status,target_types,
                        thresholds,created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (suite_id,version) DO UPDATE SET
                       name=EXCLUDED.name,description=EXCLUDED.description,
                       status=EXCLUDED.status,target_types=EXCLUDED.target_types,
                       thresholds=EXCLUDED.thresholds,updated_at=clock_timestamp()""",
                (
                    suite["suite_id"],
                    suite["version"],
                    suite["name"],
                    suite.get("description", ""),
                    suite.get("status", "active"),
                    Jsonb(suite["target_types"]),
                    Jsonb(suite.get("thresholds") or {}),
                    suite["created_by"],
                ),
            )
            conn.execute(
                "DELETE FROM eval_cases WHERE suite_id=%s AND suite_version=%s",
                (suite["suite_id"], suite["version"]),
            )
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO eval_cases
                           (suite_id,suite_version,case_id,name,input,expected,
                            scorers,tags,min_score,position)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [
                        (
                            suite["suite_id"],
                            suite["version"],
                            case["case_id"],
                            case["name"],
                            Jsonb(case.get("input") or {}),
                            Jsonb(case.get("expected")),
                            Jsonb(case["scorers"]),
                            Jsonb(case.get("tags") or []),
                            float(case.get("min_score", 1.0)),
                            index,
                        )
                        for index, case in enumerate(cases)
                    ],
                )
            return self._eval_suite(conn, suite["suite_id"], suite["version"])

    def get_eval_suite(self, suite_id: str, version: int) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM eval_suites WHERE suite_id=%s AND version=%s",
                (suite_id, version),
            ).fetchone()
            return self._eval_suite(conn, suite_id, version) if row else None

    def list_eval_suites(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT suite_id,version FROM eval_suites ORDER BY suite_id,version DESC"
            ).fetchall()
            return [
                self._eval_suite(conn, str(row["suite_id"]), int(row["version"]))
                for row in rows
            ]

    def create_eval_run(self, *, value: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self._pool.connection() as conn, conn.transaction():
            existing = conn.execute(
                "SELECT * FROM eval_runs WHERE request_hash=%s", (value["request_hash"],)
            ).fetchone()
            if existing is not None:
                return self._eval_run(conn, existing), False
            suite = conn.execute(
                """SELECT * FROM eval_suites WHERE suite_id=%s AND version=%s
                   AND status='active'""",
                (value["suite_id"], value["suite_version"]),
            ).fetchone()
            if suite is None:
                raise ValueError("active evaluation suite not found")
            if value["target_type"] not in list(suite["target_types"] or []):
                raise ValueError("evaluation suite does not support this target type")
            row = conn.execute(
                """INSERT INTO eval_runs
                       (eval_run_id,suite_id,suite_version,target_type,target_id,
                        target_revision_id,request_hash,created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    value["eval_run_id"],
                    value["suite_id"],
                    value["suite_version"],
                    value["target_type"],
                    value["target_id"],
                    value["target_revision_id"],
                    value["request_hash"],
                    value["created_by"],
                ),
            ).fetchone()
            assert row is not None
            return self._eval_run(conn, row), True

    def record_eval_case_result(
        self, eval_run_id: str, *, result: dict[str, Any]
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            run = conn.execute(
                "SELECT * FROM eval_runs WHERE eval_run_id=%s FOR UPDATE", (eval_run_id,)
            ).fetchone()
            if run is None or str(run["status"]) != "running":
                raise ValueError("evaluation run is not accepting observations")
            case = conn.execute(
                """SELECT 1 FROM eval_cases WHERE suite_id=%s AND suite_version=%s
                   AND case_id=%s""",
                (run["suite_id"], run["suite_version"], result["case_id"]),
            ).fetchone()
            if case is None:
                raise ValueError("evaluation case does not belong to run suite")
            row = conn.execute(
                """INSERT INTO eval_case_results
                       (eval_run_id,case_id,status,score,output,scorer_results,
                        metrics,error)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (eval_run_id,case_id) DO UPDATE SET
                       status=EXCLUDED.status,score=EXCLUDED.score,
                       output=EXCLUDED.output,scorer_results=EXCLUDED.scorer_results,
                       metrics=EXCLUDED.metrics,error=EXCLUDED.error,
                       updated_at=clock_timestamp()
                   RETURNING *""",
                (
                    eval_run_id,
                    result["case_id"],
                    result["status"],
                    result["score"],
                    Jsonb(result.get("output")),
                    Jsonb(result["scorer_results"]),
                    Jsonb(result.get("metrics") or {}),
                    Jsonb(result.get("error")) if result.get("error") else None,
                ),
            ).fetchone()
            assert row is not None
            return self._eval_case_result(row)

    def finalize_eval_run(self, eval_run_id: str) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            run = conn.execute(
                "SELECT * FROM eval_runs WHERE eval_run_id=%s FOR UPDATE", (eval_run_id,)
            ).fetchone()
            if run is None:
                raise ValueError("evaluation run not found")
            if str(run["status"]) in {"passed", "failed"}:
                return self._eval_run(conn, run)
            suite = conn.execute(
                "SELECT * FROM eval_suites WHERE suite_id=%s AND version=%s",
                (run["suite_id"], run["suite_version"]),
            ).fetchone()
            results = conn.execute(
                "SELECT * FROM eval_case_results WHERE eval_run_id=%s ORDER BY case_id",
                (eval_run_id,),
            ).fetchall()
            expected = conn.execute(
                """SELECT count(*) AS count FROM eval_cases
                   WHERE suite_id=%s AND suite_version=%s""",
                (run["suite_id"], run["suite_version"]),
            ).fetchone()
            if len(results) != int(expected["count"]):
                raise ValueError("evaluation run has missing case observations")
            passed_count = sum(str(item["status"]) == "passed" for item in results)
            case_count = len(results)
            pass_rate = passed_count / case_count if case_count else 0.0
            average_score = (
                sum(float(item["score"]) for item in results) / case_count
                if case_count
                else 0.0
            )
            latency_values = sorted(
                float(item["metrics"]["latency_ms"])
                for item in results
                if (item["metrics"] or {}).get("latency_ms") is not None
            )
            cost_values = [
                float(item["metrics"]["cost_usd"])
                for item in results
                if (item["metrics"] or {}).get("cost_usd") is not None
            ]
            latency_count = len(latency_values)
            cost_count = len(cost_values)
            latency_index = max(0, int((latency_count * 0.95) + 0.999999) - 1)
            total_cost = sum(cost_values)
            average_latency = (
                sum(latency_values) / latency_count if latency_count else None
            )
            p95_latency = latency_values[latency_index] if latency_count else None
            cost_coverage = cost_count / case_count if case_count else 0.0
            thresholds = dict(suite["thresholds"] or {})
            passed = pass_rate >= float(thresholds.get("min_pass_rate", 1.0)) and (
                average_score >= float(thresholds.get("min_average_score", 0.0))
            )
            max_total_cost = thresholds.get("max_total_cost_usd")
            max_p95_latency = thresholds.get("max_p95_latency_ms")
            passed = bool(
                passed
                and cost_coverage >= float(thresholds.get("min_cost_coverage", 0.0))
                and (
                    max_total_cost is None or total_cost <= float(max_total_cost)
                )
                and (
                    max_p95_latency is None
                    or (p95_latency is not None and p95_latency <= float(max_p95_latency))
                )
            )
            metrics = {
                "case_count": case_count,
                "passed_count": passed_count,
                "failed_count": case_count - passed_count,
                "pass_rate": pass_rate,
                "average_score": average_score,
                "total_cost_usd": total_cost,
                "cost_observed_count": cost_count,
                "cost_coverage": cost_coverage,
                "average_latency_ms": average_latency,
                "p95_latency_ms": p95_latency,
                "latency_observed_count": latency_count,
                "thresholds": thresholds,
            }
            row = conn.execute(
                """UPDATE eval_runs SET status=%s,metrics=%s,
                       finished_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE eval_run_id=%s RETURNING *""",
                ("passed" if passed else "failed", Jsonb(metrics), eval_run_id),
            ).fetchone()
            assert row is not None
            return self._eval_run(conn, row)

    def list_eval_runs(
        self,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM eval_runs
                   WHERE (%s::text IS NULL OR target_type=%s)
                     AND (%s::text IS NULL OR target_id=%s)
                   ORDER BY created_at DESC LIMIT %s""",
                (target_type, target_type, target_id, target_id, max(1, min(limit, 1000))),
            ).fetchall()
            return [self._eval_run(conn, row) for row in rows]

    def get_eval_run(self, eval_run_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM eval_runs WHERE eval_run_id=%s", (eval_run_id,)
            ).fetchone()
            return self._eval_run(conn, row) if row else None

    @staticmethod
    def _eval_suite(conn: Any, suite_id: str, version: int) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        row = conn.execute(
            "SELECT * FROM eval_suites WHERE suite_id=%s AND version=%s",
            (suite_id, version),
        ).fetchone()
        cases = conn.execute(
            """SELECT * FROM eval_cases WHERE suite_id=%s AND suite_version=%s
               ORDER BY position,case_id""",
            (suite_id, version),
        ).fetchall()
        return {
            "suite_id": str(row["suite_id"]),
            "version": int(row["version"]),
            "name": str(row["name"]),
            "description": str(row["description"]),
            "status": str(row["status"]),
            "target_types": list(row["target_types"] or []),
            "thresholds": dict(row["thresholds"] or {}),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "cases": [
                {
                    "case_id": str(item["case_id"]),
                    "name": str(item["name"]),
                    "input": dict(item["input"] or {}),
                    "expected": item["expected"],
                    "scorers": list(item["scorers"] or []),
                    "tags": list(item["tags"] or []),
                    "min_score": float(item["min_score"]),
                }
                for item in cases
            ],
        }

    def _eval_run(self, conn: Any, row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        results = conn.execute(
            "SELECT * FROM eval_case_results WHERE eval_run_id=%s ORDER BY case_id",
            (row["eval_run_id"],),
        ).fetchall()
        job = conn.execute(
            "SELECT * FROM eval_execution_jobs WHERE eval_run_id=%s",
            (row["eval_run_id"],),
        ).fetchone()
        return {
            "eval_run_id": str(row["eval_run_id"]),
            "suite_id": str(row["suite_id"]),
            "suite_version": int(row["suite_version"]),
            "target_type": str(row["target_type"]),
            "target_id": str(row["target_id"]),
            "target_revision_id": str(row["target_revision_id"]),
            "status": str(row["status"]),
            "metrics": dict(row["metrics"] or {}),
            "error": dict(row["error"] or {}) or None,
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "finished_at": _iso(row["finished_at"]),
            "execution_job": self._eval_job(job) if job else None,
            "results": [self._eval_case_result(item) for item in results],
        }

    @staticmethod
    def _eval_case_result(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "eval_run_id": str(row["eval_run_id"]),
            "case_id": str(row["case_id"]),
            "status": str(row["status"]),
            "score": float(row["score"]),
            "output": row["output"],
            "scorer_results": list(row["scorer_results"] or []),
            "metrics": dict(row["metrics"] or {}),
            "error": dict(row["error"] or {}) or None,
            "created_at": _iso(row["created_at"]),
        }
