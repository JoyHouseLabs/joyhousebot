"""Release-gate policies and immutable Eval evidence decisions."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.json_codec import Jsonb


class PostgresEvalGateStoreMixin:
    def save_release_gate_policy(self, *, value: dict[str, Any]) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO release_gate_policies
                       (target_type,target_id,target_revision_id,required,
                        requirements,created_by)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (target_type,target_id,target_revision_id) DO UPDATE SET
                       required=EXCLUDED.required,requirements=EXCLUDED.requirements,
                       created_by=EXCLUDED.created_by,updated_at=clock_timestamp()""",
                (
                    value["target_type"],
                    value["target_id"],
                    value["target_revision_id"],
                    value.get("required", True),
                    Jsonb(value["requirements"]),
                    value["created_by"],
                ),
            )
        policy = self.get_release_gate_policy(
            value["target_type"], value["target_id"], value["target_revision_id"]
        )
        assert policy is not None
        return policy

    def get_release_gate_policy(
        self, target_type: str, target_id: str, target_revision_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM release_gate_policies WHERE target_type=%s
                   AND target_id=%s AND target_revision_id=%s""",
                (target_type, target_id, target_revision_id),
            ).fetchone()
        if row is None:
            return None
        from joyhousebot.storage.postgres_store import _iso

        return {
            "target_type": str(row["target_type"]),
            "target_id": str(row["target_id"]),
            "target_revision_id": str(row["target_revision_id"]),
            "required": bool(row["required"]),
            "requirements": list(row["requirements"] or []),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }

    def evaluate_release_gate(
        self,
        *,
        target_type: str,
        target_id: str,
        target_revision_id: str,
        purpose: str,
        actor_id: str,
        decision_id: str,
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            policy = conn.execute(
                """SELECT * FROM release_gate_policies WHERE target_type=%s
                   AND target_id=%s AND target_revision_id=%s""",
                (target_type, target_id, target_revision_id),
            ).fetchone()
            if policy is None or not bool(policy["required"]):
                return {"required": False, "passed": True, "requirements": []}
            evidence: list[dict[str, Any]] = []
            passed = True
            for requirement in list(policy["requirements"] or []):
                suite_id = str(requirement["suite_id"])
                suite_version = int(requirement["suite_version"])
                min_pass_rate = float(requirement.get("min_pass_rate", 1.0))
                max_age_hours = max(1, int(requirement.get("max_age_hours", 168)))
                require_automated = bool(requirement.get("require_automated", False))
                max_total_cost = requirement.get("max_total_cost_usd")
                max_p95_latency = requirement.get("max_p95_latency_ms")
                min_cost_coverage = float(requirement.get("min_cost_coverage", 0.0))
                run = conn.execute(
                    """SELECT * FROM eval_runs WHERE suite_id=%s AND suite_version=%s
                       AND target_type=%s AND target_id=%s AND target_revision_id=%s
                       AND status IN ('passed','failed')
                       AND finished_at>=clock_timestamp()-(%s*interval '1 hour')
                       ORDER BY finished_at DESC LIMIT 1""",
                    (
                        suite_id,
                        suite_version,
                        target_type,
                        target_id,
                        target_revision_id,
                        max_age_hours,
                    ),
                ).fetchone()
                actual_rate = (
                    float((run["metrics"] or {}).get("pass_rate", 0.0)) if run else 0.0
                )
                run_metrics = dict(run["metrics"] or {}) if run else {}
                actual_total_cost = float(run_metrics.get("total_cost_usd", 0.0))
                actual_cost_coverage = float(run_metrics.get("cost_coverage", 0.0))
                actual_p95_latency = run_metrics.get("p95_latency_ms")
                automated = False
                if run is not None:
                    modes = conn.execute(
                        """SELECT count(*) AS count,
                                  bool_and(metrics->>'execution_mode'='automated') AS automated
                           FROM eval_case_results WHERE eval_run_id=%s""",
                        (run["eval_run_id"],),
                    ).fetchone()
                    automated = bool(modes["count"] and modes["automated"])
                requirement_passed = bool(
                    run
                    and str(run["status"]) == "passed"
                    and actual_rate >= min_pass_rate
                    and actual_cost_coverage >= min_cost_coverage
                    and (
                        max_total_cost is None
                        or actual_total_cost <= float(max_total_cost)
                    )
                    and (
                        max_p95_latency is None
                        or (
                            actual_p95_latency is not None
                            and float(actual_p95_latency) <= float(max_p95_latency)
                        )
                    )
                    and (automated or not require_automated)
                )
                passed = passed and requirement_passed
                evidence.append(
                    {
                        "suite_id": suite_id,
                        "suite_version": suite_version,
                        "eval_run_id": str(run["eval_run_id"]) if run else None,
                        "status": str(run["status"]) if run else "missing",
                        "pass_rate": actual_rate,
                        "min_pass_rate": min_pass_rate,
                        "max_age_hours": max_age_hours,
                        "total_cost_usd": actual_total_cost,
                        "max_total_cost_usd": max_total_cost,
                        "cost_coverage": actual_cost_coverage,
                        "min_cost_coverage": min_cost_coverage,
                        "p95_latency_ms": actual_p95_latency,
                        "max_p95_latency_ms": max_p95_latency,
                        "require_automated": require_automated,
                        "automated": automated,
                        "passed": requirement_passed,
                    }
                )
            result = {"required": True, "passed": passed, "requirements": evidence}
            conn.execute(
                """INSERT INTO release_gate_decisions
                       (decision_id,target_type,target_id,target_revision_id,purpose,
                        passed,evidence,actor_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (
                    decision_id,
                    target_type,
                    target_id,
                    target_revision_id,
                    purpose,
                    passed,
                    Jsonb(result),
                    actor_id,
                ),
            )
            return result
