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
            capacity = conn.execute(
                """SELECT
                    count(*) AS reporting_workers,
                    COALESCE(sum(CASE WHEN capabilities @> '{"agent": true}'::jsonb
                        AND COALESCE(metadata #>> '{capacity,agent,slots}','')
                        ~ '^[0-9]+$' THEN (metadata #>> '{capacity,agent,slots}')::integer END),0)
                        AS agent_slots,
                    COALESCE(sum(CASE WHEN capabilities @> '{"agent": true}'::jsonb
                        AND COALESCE(metadata #>> '{capacity,agent,active}','')
                        ~ '^[0-9]+$' THEN (metadata #>> '{capacity,agent,active}')::integer END),0)
                        AS agent_active,
                    COALESCE(sum(CASE WHEN capabilities @> '{"agent": true}'::jsonb
                        AND COALESCE(metadata #>> '{capacity,agent,waiting}','')
                        ~ '^[0-9]+$' THEN (metadata #>> '{capacity,agent,waiting}')::integer END),0)
                        AS agent_waiting,
                    COALESCE(sum(CASE WHEN capabilities @> '{"agent": true}'::jsonb
                        AND COALESCE(metadata #>> '{capacity,graph,slots}','')
                        ~ '^[0-9]+$' THEN (metadata #>> '{capacity,graph,slots}')::integer END),0)
                        AS graph_slots,
                    COALESCE(sum(CASE WHEN capabilities @> '{"agent": true}'::jsonb
                        AND COALESCE(metadata #>> '{capacity,graph,active}','')
                        ~ '^[0-9]+$' THEN (metadata #>> '{capacity,graph,active}')::integer END),0)
                        AS graph_active,
                    COALESCE(sum(CASE WHEN capabilities @> '{"agent": true}'::jsonb
                        AND COALESCE(metadata #>> '{capacity,graph,buffered}','')
                        ~ '^[0-9]+$' THEN (metadata #>> '{capacity,graph,buffered}')::integer END),0)
                        AS graph_buffered,
                    COALESCE(avg(CASE WHEN COALESCE(metadata #>> '{process,cpu_percent}','')
                        ~ '^[0-9]+(\\.[0-9]+)?$' THEN (metadata #>> '{process,cpu_percent}')::double precision END),0)
                        AS worker_cpu_percent_avg,
                    COALESCE(sum(CASE WHEN COALESCE(metadata #>> '{process,rss_bytes}','')
                        ~ '^[0-9]+$' THEN (metadata #>> '{process,rss_bytes}')::bigint END),0)
                        AS worker_rss_bytes
                   FROM runtime_workers
                   WHERE status='online'
                     AND last_heartbeat>clock_timestamp()-interval '30 seconds'"""
            ).fetchone()
            provider_errors = conn.execute(
                """SELECT provider,model,count(*) AS total,
                          count(*) FILTER (WHERE status='failed') AS failed
                   FROM model_invocations
                   WHERE started_at>clock_timestamp()-interval '24 hours'
                   GROUP BY provider,model ORDER BY failed DESC,total DESC,provider,model
                   LIMIT 100"""
            ).fetchall()
            providers = conn.execute(
                """SELECT provider,model,status,count(*) AS count,
                          COALESCE(avg(duration_ms),0) AS avg_duration_ms,
                          COALESCE(avg(ttft_ms),0) AS avg_ttft_ms,
                          COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms)
                              FILTER (WHERE duration_ms IS NOT NULL),0) AS p95_duration_ms,
                          COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms)
                              FILTER (WHERE ttft_ms IS NOT NULL),0) AS p95_ttft_ms,
                          COALESCE(sum((usage->>'input_tokens')::bigint),0)
                              AS input_tokens,
                          COALESCE(sum((usage->>'output_tokens')::bigint),0)
                              AS output_tokens,
                          COALESCE(sum(COALESCE(
                              (usage->>'billed_input_tokens')::bigint,
                              CASE WHEN cache_status='hit' THEN 0
                                   ELSE (usage->>'input_tokens')::bigint END)),0)
                              AS billed_input_tokens,
                          COALESCE(sum(COALESCE(
                              (usage->>'billed_output_tokens')::bigint,
                              CASE WHEN cache_status='hit' THEN 0
                                   ELSE (usage->>'output_tokens')::bigint END)),0)
                              AS billed_output_tokens,
                          count(*) FILTER (WHERE
                              COALESCE(usage->>'usage_status',CASE
                                  WHEN usage ? 'input_tokens' OR usage ? 'output_tokens'
                                      THEN 'exact' ELSE 'missing' END)='missing')
                              AS missing_usage_invocations,
                          count(*) FILTER (WHERE
                              COALESCE(usage->>'billing_status',CASE
                                  WHEN cache_status='hit' THEN 'not_billed'
                                  WHEN cost_usd<>0 THEN 'exact'
                                  ELSE 'missing' END)='missing')
                              AS missing_billing_invocations,
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
            team_runs = conn.execute(
                """SELECT status,count(*) AS count FROM runtime_runs
                   WHERE options->'metadata' ? 'team_ref'
                     AND created_at>clock_timestamp()-interval '24 hours'
                   GROUP BY status ORDER BY status"""
            ).fetchall()
            team_plan_actions = conn.execute(
                """SELECT status,count(*) AS count FROM run_plan_confirmations
                   WHERE requested_at>clock_timestamp()-interval '24 hours'
                   GROUP BY status ORDER BY status"""
            ).fetchall()
            team_plan_wait = conn.execute(
                """SELECT COALESCE(percentile_disc(0.95) WITHIN GROUP (
                          ORDER BY EXTRACT(EPOCH FROM (action_at-requested_at))),0)
                       AS wait_p95,
                      COALESCE(avg(EXTRACT(EPOCH FROM (action_at-requested_at))),0)
                       AS wait_avg
                   FROM run_plan_confirmations
                   WHERE action_at IS NOT NULL
                     AND requested_at>clock_timestamp()-interval '24 hours'"""
            ).fetchone()
            team_planning = conn.execute(
                """SELECT COALESCE(percentile_disc(0.95) WITHIN GROUP (
                          ORDER BY EXTRACT(EPOCH FROM (artifact.created_at-run.created_at))),0)
                       AS planning_p95,
                      COALESCE(avg(EXTRACT(EPOCH FROM (artifact.created_at-run.created_at))),0)
                       AS planning_avg
                   FROM runtime_runs AS run
                   JOIN runtime_artifacts AS artifact
                     ON artifact.run_id=run.run_id AND artifact.name='coordinator-plan'
                   WHERE run.options->'metadata' ? 'team_ref'
                     AND run.created_at>clock_timestamp()-interval '24 hours'"""
            ).fetchone()
            team_tasks = conn.execute(
                """SELECT payload->'metadata'->'team_step_contract'->>'kind' AS kind,
                          status,count(*) AS count
                   FROM runtime_tasks
                   WHERE payload->'metadata' ? 'team_step_contract'
                     AND created_at>clock_timestamp()-interval '24 hours'
                   GROUP BY 1,2 ORDER BY 1,2"""
            ).fetchall()
            coordinator_replans = conn.execute(
                """SELECT reason_code,count(*) AS count FROM loop_decisions
                   WHERE decision IN ('replan','escalate')
                     AND created_at>clock_timestamp()-interval '24 hours'
                   GROUP BY reason_code ORDER BY count DESC, reason_code LIMIT 25"""
            ).fetchall()
        pool_stats = self._pool.get_stats()
        metrics.update(
            {
                "queue": {
                    "queued": int(queue["queued"] or 0),
                    "oldest_age_seconds": float(queue["oldest_age_seconds"] or 0),
                    "expired_leases": int(queue["expired_leases"] or 0),
                    "retried_tasks": int(queue["retried"] or 0),
                    "claim_delay_p95_ms": float(queue["claim_delay_p95_ms"] or 0),
                },
                "capacity": {
                    "reporting_workers": int(capacity["reporting_workers"] or 0),
                    "agent_slots": int(capacity["agent_slots"] or 0),
                    "agent_active": int(capacity["agent_active"] or 0),
                    "agent_waiting": int(capacity["agent_waiting"] or 0),
                    "graph_slots": int(capacity["graph_slots"] or 0),
                    "graph_active": int(capacity["graph_active"] or 0),
                    "graph_buffered": int(capacity["graph_buffered"] or 0),
                    "worker_cpu_percent_avg": float(capacity["worker_cpu_percent_avg"] or 0),
                    "worker_rss_bytes": int(capacity["worker_rss_bytes"] or 0),
                },
                "database_pool": {
                    "min_size": int(pool_stats.get("pool_min", 0)),
                    "max_size": int(pool_stats.get("pool_max", 0)),
                    "size": int(pool_stats.get("pool_size", 0)),
                    "available": int(pool_stats.get("pool_available", 0)),
                    "waiting": int(pool_stats.get("requests_waiting", 0)),
                },
                "provider_errors_24h": [
                    {
                        "provider": str(row["provider"]),
                        "model": str(row["model"]),
                        "total": int(row["total"]),
                        "failed": int(row["failed"]),
                        "failure_rate": round(
                            int(row["failed"]) / max(1, int(row["total"])) * 100, 1
                        ),
                    }
                    for row in provider_errors
                ],
                "runs_24h": [
                    {
                        "status": str(row["status"]),
                        "count": int(row["count"]),
                        "p95_duration_ms": float(row["p95_duration_ms"] or 0),
                    }
                    for row in recent_runs
                ],
                "team_runs": [
                    {"status": str(row["status"]), "count": int(row["count"])}
                    for row in team_runs
                ],
                "team_plan_actions": [
                    {"action": str(row["status"]), "count": int(row["count"])}
                    for row in team_plan_actions
                ],
                "team_planning": {
                    "planning_duration_seconds_p95": float(
                        team_planning["planning_p95"] or 0
                    ),
                    "planning_duration_seconds_avg": float(
                        team_planning["planning_avg"] or 0
                    ),
                    "confirmation_wait_seconds_p95": float(
                        team_plan_wait["wait_p95"] or 0
                    ),
                },
                "team_tasks": [
                    {
                        "kind": str(row["kind"] or "unknown"),
                        "status": str(row["status"]),
                        "count": int(row["count"]),
                    }
                    for row in team_tasks
                ],
                "coordinator_replans": [
                    {"reason_code": str(row["reason_code"]), "count": int(row["count"])}
                    for row in coordinator_replans
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
                "input_tokens": int(row["input_tokens"] or 0),
                "output_tokens": int(row["output_tokens"] or 0),
                "billed_input_tokens": int(row["billed_input_tokens"] or 0),
                "billed_output_tokens": int(row["billed_output_tokens"] or 0),
                "missing_usage_invocations": int(
                    row["missing_usage_invocations"] or 0
                ),
                "missing_billing_invocations": int(
                    row["missing_billing_invocations"] or 0
                ),
                "cost_usd": float(row["cost_usd"] or 0),
            }
            for row in providers
        ]
        return metrics
