"""Bounded PostgreSQL aggregates for production metrics and SLOs."""

from __future__ import annotations

from typing import Any


def _counts(rows: list[Any]) -> dict[str, int]:
    return {str(row["status"]): int(row["count"]) for row in rows}


class PostgresOperationalMetricsStoreMixin:
    """Read low-cardinality metrics from the durable runtime fact tables."""

    def runtime_metrics(self) -> dict[str, Any]:
        with self._pool.connection() as conn:
            runs = conn.execute(
                "SELECT status,count(*) AS count FROM runtime_runs GROUP BY status ORDER BY status"
            ).fetchall()
            tasks = conn.execute(
                "SELECT status,count(*) AS count FROM runtime_tasks GROUP BY status ORDER BY status"
            ).fetchall()
            workers = conn.execute(
                "SELECT status,count(*) AS count FROM runtime_workers GROUP BY status ORDER BY status"
            ).fetchall()
        return {"runs": _counts(runs), "tasks": _counts(tasks), "workers": _counts(workers)}

    def operational_metrics(self) -> dict[str, Any]:
        """Return a cacheable metrics snapshot without unbounded labels."""
        metrics = self.runtime_metrics()
        with self._pool.connection() as conn:
            queue = conn.execute(
                """SELECT count(*) FILTER (WHERE status='queued') AS queued,
                          COALESCE(EXTRACT(EPOCH FROM (clock_timestamp() -
                              min(available_at) FILTER (WHERE status='queued'))),0)
                              AS oldest_age_seconds,
                          count(*) FILTER (WHERE status='running'
                              AND lease_expires_at<clock_timestamp()) AS expired_leases,
                          count(*) FILTER (WHERE attempt>1) AS retried,
                          COALESCE(percentile_cont(0.95) WITHIN GROUP
                              (ORDER BY EXTRACT(EPOCH FROM (started_at-created_at))*1000)
                              FILTER (WHERE started_at IS NOT NULL
                                  AND started_at>clock_timestamp()-interval '24 hours'),0)
                              AS claim_delay_p95_ms
                   FROM runtime_tasks"""
            ).fetchone()
            providers = conn.execute(
                """SELECT provider,model,status,count(*) AS count,
                          COALESCE(avg(duration_ms),0) AS avg_duration_ms,
                          COALESCE(avg(ttft_ms),0) AS avg_ttft_ms,
                          COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)
                              FILTER (WHERE duration_ms IS NOT NULL),0) AS p95_duration_ms,
                          COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms)
                              FILTER (WHERE ttft_ms IS NOT NULL),0) AS p95_ttft_ms,
                          COALESCE(sum(cost_usd),0) AS cost_usd
                   FROM model_invocations GROUP BY provider,model,status
                   ORDER BY provider,model,status LIMIT 500"""
            ).fetchall()
            recent_runs = conn.execute(
                """SELECT status,count(*) AS count,
                          COALESCE(percentile_cont(0.95) WITHIN GROUP
                              (ORDER BY EXTRACT(EPOCH FROM (finished_at-started_at))*1000)
                              FILTER (WHERE started_at IS NOT NULL
                                  AND finished_at IS NOT NULL),0) AS p95_duration_ms
                   FROM runtime_runs
                   WHERE created_at>clock_timestamp()-interval '24 hours'
                   GROUP BY status ORDER BY status"""
            ).fetchall()
            actions = conn.execute(
                "SELECT status,count(*) AS count FROM action_intents GROUP BY status ORDER BY status"
            ).fetchall()
            approvals = conn.execute(
                """SELECT status,count(*) AS count FROM approval_requests
                   GROUP BY status ORDER BY status"""
            ).fetchall()
            approval_age = conn.execute(
                """SELECT COALESCE(EXTRACT(EPOCH FROM
                          (clock_timestamp()-min(requested_at))),0) AS seconds
                   FROM approval_requests WHERE status='pending'"""
            ).fetchone()
            reconciliations = conn.execute(
                """SELECT status,count(*) AS count FROM operation_reconciliations
                   GROUP BY status ORDER BY status"""
            ).fetchall()
            reconciliation_age = conn.execute(
                """SELECT COALESCE(EXTRACT(EPOCH FROM
                          (clock_timestamp()-min(created_at))),0) AS seconds
                   FROM operation_reconciliations
                   WHERE status IN ('pending','checking','manual_required')"""
            ).fetchone()
            verifications = conn.execute(
                """SELECT status,verifier_type,count(*) AS count
                   FROM verification_records GROUP BY status,verifier_type
                   ORDER BY status,verifier_type LIMIT 100"""
            ).fetchall()
            sagas = conn.execute(
                "SELECT status,count(*) AS count FROM graph_sagas GROUP BY status ORDER BY status"
            ).fetchall()
            patch_proposals = conn.execute(
                """SELECT status,count(*) AS count FROM graph_patch_proposals
                   GROUP BY status ORDER BY status"""
            ).fetchall()
            eval_runs = conn.execute(
                "SELECT status,count(*) AS count FROM eval_runs GROUP BY status ORDER BY status"
            ).fetchall()
            api_tokens = conn.execute(
                """SELECT token_type,
                          CASE WHEN enabled THEN 'active' ELSE 'revoked' END AS status,
                          count(*) AS count
                   FROM api_access_tokens GROUP BY token_type,enabled
                   ORDER BY token_type,enabled"""
            ).fetchall()
            token_risks = conn.execute(
                """SELECT
                     count(*) FILTER (WHERE enabled AND rotation_due_at IS NOT NULL
                       AND rotation_due_at<=clock_timestamp()) AS rotation_overdue,
                     count(*) FILTER (WHERE enabled AND expires_at IS NOT NULL
                       AND expires_at<=clock_timestamp()+interval '7 days') AS expiring_7d,
                     count(*) FILTER (WHERE enabled AND expires_at IS NULL) AS indefinite
                   FROM api_access_tokens"""
            ).fetchone()
            works = conn.execute(
                """SELECT status,visibility,count(*) AS count FROM works
                   GROUP BY status,visibility ORDER BY status,visibility"""
            ).fetchall()
            shares = conn.execute(
                "SELECT status,count(*) AS count FROM work_shares GROUP BY status ORDER BY status"
            ).fetchall()
            critical_events = conn.execute(
                """SELECT event_type,count(*) AS count FROM runtime_events
                   WHERE created_at>clock_timestamp()-interval '24 hours'
                     AND event_type=ANY(%s)
                   GROUP BY event_type ORDER BY event_type""",
                (
                    [
                        "lease.lost",
                        "lease.takeover",
                        "loop.stalled",
                        "loop.exhausted",
                        "capability.failed",
                        "verification.failed",
                        "compensation.failed",
                        "saga.failed",
                    ],
                ),
            ).fetchall()
            active_ages = conn.execute(
                """SELECT
                    COALESCE((SELECT EXTRACT(EPOCH FROM
                        (clock_timestamp()-min(created_at))) FROM runtime_runs
                        WHERE status IN ('queued','running','planning')),0)
                        AS run_seconds,
                    COALESCE((SELECT EXTRACT(EPOCH FROM
                        (clock_timestamp()-min(started_at))) FROM verification_records
                        WHERE status='running'),0) AS verification_seconds,
                    COALESCE((SELECT EXTRACT(EPOCH FROM
                        (clock_timestamp()-min(started_at))) FROM graph_sagas
                        WHERE status='running'),0) AS saga_seconds,
                    COALESCE((SELECT EXTRACT(EPOCH FROM
                        (clock_timestamp()-min(created_at))) FROM graph_patch_proposals
                        WHERE status IN ('pending','activating')),0) AS patch_seconds,
                    COALESCE((SELECT EXTRACT(EPOCH FROM
                        (clock_timestamp()-min(created_at))) FROM eval_runs
                        WHERE status='running'),0) AS eval_seconds"""
            ).fetchone()
            stale_workers = conn.execute(
                """SELECT count(*) AS count FROM runtime_workers
                   WHERE status='online'
                     AND last_heartbeat<clock_timestamp()-interval '30 seconds'"""
            ).fetchone()
            outbox = (
                conn.execute(
                    """SELECT channel,status,count(*) AS count FROM channel_outbox
                       GROUP BY channel,status ORDER BY channel,status LIMIT 500"""
                ).fetchall()
                if conn.execute("SELECT to_regclass('public.channel_outbox') AS name").fetchone()[
                    "name"
                ]
                else []
            )
            app_callback_deliveries = conn.execute(
                """SELECT status,count(*) AS count FROM app_callback_outbox
                   GROUP BY status ORDER BY status"""
            ).fetchall()
            app_callback_age = conn.execute(
                """SELECT COALESCE(EXTRACT(EPOCH FROM
                          (clock_timestamp()-min(created_at))),0) AS seconds
                   FROM app_callback_outbox WHERE status IN ('pending','sending')"""
            ).fetchone()
        metrics.update(
            {
                "queue": {
                    "queued": int(queue["queued"] or 0),
                    "oldest_age_seconds": float(queue["oldest_age_seconds"] or 0),
                    "expired_leases": int(queue["expired_leases"] or 0),
                    "retried_tasks": int(queue["retried"] or 0),
                    "claim_delay_p95_ms": float(queue["claim_delay_p95_ms"] or 0),
                },
                "runs_24h": [
                    {
                        "status": str(row["status"]),
                        "count": int(row["count"]),
                        "p95_duration_ms": float(row["p95_duration_ms"] or 0),
                    }
                    for row in recent_runs
                ],
                "actions": _counts(actions),
                "approvals": _counts(approvals),
                "approval_oldest_pending_seconds": float(approval_age["seconds"] or 0),
                "reconciliations": _counts(reconciliations),
                "reconciliation_oldest_active_seconds": float(
                    reconciliation_age["seconds"] or 0
                ),
                "verifications": [
                    {
                        "status": str(row["status"]),
                        "verifier_type": str(row["verifier_type"]),
                        "count": int(row["count"]),
                    }
                    for row in verifications
                ],
                "sagas": _counts(sagas),
                "graph_patch_proposals": _counts(patch_proposals),
                "eval_runs": _counts(eval_runs),
                "api_tokens": [
                    {
                        "token_type": str(row["token_type"]),
                        "status": str(row["status"]),
                        "count": int(row["count"]),
                    }
                    for row in api_tokens
                ],
                "api_token_risks": {
                    "rotation_overdue": int(token_risks["rotation_overdue"] or 0),
                    "expiring_7d": int(token_risks["expiring_7d"] or 0),
                    "indefinite": int(token_risks["indefinite"] or 0),
                },
                "works": [
                    {
                        "status": str(row["status"]),
                        "visibility": str(row["visibility"]),
                        "count": int(row["count"]),
                    }
                    for row in works
                ],
                "work_shares": _counts(shares),
                "critical_events_24h": {
                    str(row["event_type"]): int(row["count"]) for row in critical_events
                },
                "active_ages": {
                    "run": float(active_ages["run_seconds"] or 0),
                    "verification": float(active_ages["verification_seconds"] or 0),
                    "saga": float(active_ages["saga_seconds"] or 0),
                    "graph_patch": float(active_ages["patch_seconds"] or 0),
                    "eval": float(active_ages["eval_seconds"] or 0),
                },
                "workers_stale": int(stale_workers["count"] or 0),
                "channels": [
                    {
                        "channel": str(row["channel"]),
                        "status": str(row["status"]),
                        "count": int(row["count"]),
                    }
                    for row in outbox
                ],
                "app_callback_deliveries": _counts(app_callback_deliveries),
                "app_callback_oldest_pending_seconds": float(
                    app_callback_age["seconds"] or 0
                ),
            }
        )
        metrics["providers"] = [
            {
                "provider": str(row["provider"]),
                "model": str(row["model"]),
                "status": str(row["status"]),
                "count": int(row["count"]),
                "avg_duration_ms": float(row["avg_duration_ms"] or 0),
                "avg_ttft_ms": float(row["avg_ttft_ms"] or 0),
                "p95_duration_ms": float(row["p95_duration_ms"] or 0),
                "p95_ttft_ms": float(row["p95_ttft_ms"] or 0),
                "cost_usd": float(row["cost_usd"] or 0),
            }
            for row in providers
        ]
        return metrics
