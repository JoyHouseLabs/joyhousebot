"""Read-only App installation usage and model-cost attribution."""

from __future__ import annotations

from datetime import datetime
from typing import Any


class PostgresAppUsageStoreMixin:
    def get_user_model_usage(self, user_id: str) -> dict[str, Any]:
        """Return authoritative lifetime usage from the invocation ledger."""
        with self._pool.connection() as conn:
            run_row = conn.execute(
                "SELECT COUNT(*) AS runs FROM runtime_runs WHERE user_id=%s",
                (user_id,),
            ).fetchone()
            row = conn.execute(
                """SELECT COUNT(*) AS model_invocations,
                          COALESCE(SUM((mi.usage->>'input_tokens')::bigint),0)
                              AS input_tokens,
                          COALESCE(SUM((mi.usage->>'output_tokens')::bigint),0)
                              AS output_tokens,
                          COALESCE(SUM(COALESCE(
                              (mi.usage->>'billed_input_tokens')::bigint,
                              CASE WHEN mi.cache_status='hit' THEN 0
                                   ELSE (mi.usage->>'input_tokens')::bigint END)),0)
                              AS billed_input_tokens,
                          COALESCE(SUM(COALESCE(
                              (mi.usage->>'billed_output_tokens')::bigint,
                              CASE WHEN mi.cache_status='hit' THEN 0
                                   ELSE (mi.usage->>'output_tokens')::bigint END)),0)
                              AS billed_output_tokens,
                          COALESCE(SUM(mi.cost_usd),0) AS cost_usd,
                          COUNT(*) FILTER (
                              WHERE COALESCE(mi.usage->>'usage_status',CASE
                                  WHEN mi.usage ? 'input_tokens'
                                    OR mi.usage ? 'output_tokens' THEN 'exact'
                                  ELSE 'missing' END)='missing')
                              AS missing_usage_invocations,
                          COUNT(*) FILTER (
                              WHERE mi.usage->>'usage_status'='partial')
                              AS partial_usage_invocations,
                          COUNT(*) FILTER (
                              WHERE COALESCE(mi.usage->>'billing_status',CASE
                                  WHEN mi.cache_status='hit' THEN 'not_billed'
                                  WHEN mi.cost_usd<>0 THEN 'exact'
                                  ELSE 'missing' END)='missing')
                              AS missing_billing_invocations
                   FROM model_invocations mi
                   JOIN runtime_runs run ON run.run_id=mi.run_id
                   WHERE run.user_id=%s""",
                (user_id,),
            ).fetchone()
        input_tokens = int(row["input_tokens"] or 0)
        output_tokens = int(row["output_tokens"] or 0)
        billed_input = int(row["billed_input_tokens"] or 0)
        billed_output = int(row["billed_output_tokens"] or 0)
        invocations = int(row["model_invocations"] or 0)
        missing_usage = int(row["missing_usage_invocations"] or 0)
        partial_usage = int(row["partial_usage_invocations"] or 0)
        missing_billing = int(row["missing_billing_invocations"] or 0)
        return {
            "runs": int(run_row["runs"] or 0),
            "model_invocations": invocations,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "billed_input_tokens": billed_input,
            "billed_output_tokens": billed_output,
            "billed_total_tokens": billed_input + billed_output,
            "cost_usd": float(row["cost_usd"] or 0.0),
            "missing_usage_invocations": missing_usage,
            "partial_usage_invocations": partial_usage,
            "missing_billing_invocations": missing_billing,
            "usage_status": (
                "missing"
                if invocations and missing_usage >= invocations
                else "partial"
                if missing_usage or partial_usage
                else "exact"
            ),
            "billing_status": (
                "missing"
                if invocations and missing_billing >= invocations
                else "partial"
                if missing_billing
                else "exact"
            ),
        }

    def get_app_installation_usage(
        self,
        installation_id: str,
        *,
        user_id: str,
        since: datetime,
        until: datetime,
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            installation = conn.execute(
                """SELECT i.installation_id,i.app_id,i.current_version AS version,
                          r.manifest
                   FROM app_installations i JOIN app_releases r
                     ON r.app_id=i.app_id AND r.version=i.current_version
                   WHERE i.installation_id=%s AND i.user_id=%s""",
                (installation_id, user_id),
            ).fetchone()
            if installation is None:
                return None
            run_rows = conn.execute(
                """SELECT status,
                          COALESCE(app_entrypoint_id,'unknown')
                            AS entrypoint_id,
                          COUNT(*) AS count
                   FROM runtime_runs
                   WHERE user_id=%s AND created_at>=%s AND created_at<%s
                     AND app_installation_id=%s
                   GROUP BY status,entrypoint_id
                   ORDER BY entrypoint_id,status""",
                (user_id, since, until, installation_id),
            ).fetchall()
            model_rows = conn.execute(
                """WITH app_roots AS (
                     SELECT run_id FROM runtime_runs
                     WHERE user_id=%s AND created_at>=%s AND created_at<%s
                       AND app_installation_id=%s
                   ), app_runs AS (
                     SELECT run_id FROM runtime_runs
                     WHERE root_run_id IN (SELECT run_id FROM app_roots)
                   )
                   SELECT provider,model,COUNT(*) AS invocations,
                          COALESCE(SUM((usage->>'input_tokens')::bigint),0) AS input_tokens,
                          COALESCE(SUM((usage->>'output_tokens')::bigint),0) AS output_tokens,
                          COALESCE(SUM(COALESCE(
                              (usage->>'billed_input_tokens')::bigint,
                              CASE WHEN cache_status='hit' THEN 0
                                   ELSE (usage->>'input_tokens')::bigint END)),0)
                              AS billed_input_tokens,
                          COALESCE(SUM(COALESCE(
                              (usage->>'billed_output_tokens')::bigint,
                              CASE WHEN cache_status='hit' THEN 0
                                   ELSE (usage->>'output_tokens')::bigint END)),0)
                              AS billed_output_tokens,
                          COUNT(*) FILTER (WHERE
                              COALESCE(usage->>'usage_status',CASE
                                  WHEN usage ? 'input_tokens' OR usage ? 'output_tokens'
                                      THEN 'exact' ELSE 'missing' END)='missing')
                              AS missing_usage_invocations,
                          COUNT(*) FILTER (WHERE usage->>'usage_status'='partial')
                              AS partial_usage_invocations,
                          COUNT(*) FILTER (WHERE
                              COALESCE(usage->>'billing_status',CASE
                                  WHEN cache_status='hit' THEN 'not_billed'
                                  WHEN cost_usd<>0 THEN 'exact'
                                  ELSE 'missing' END)='missing')
                              AS missing_billing_invocations,
                          COALESCE(SUM(cost_usd),0) AS cost_usd
                   FROM model_invocations
                   WHERE run_id IN (SELECT run_id FROM app_runs)
                   GROUP BY provider,model ORDER BY provider,model""",
                (user_id, since, until, installation_id),
            ).fetchall()
        statuses: dict[str, int] = {}
        entrypoints: dict[str, dict[str, Any]] = {}
        for row in run_rows:
            count = int(row["count"])
            status = str(row["status"])
            entrypoint_id = str(row["entrypoint_id"])
            statuses[status] = statuses.get(status, 0) + count
            entrypoint = entrypoints.setdefault(
                entrypoint_id, {"entrypoint_id": entrypoint_id, "runs": 0, "statuses": {}}
            )
            entrypoint["runs"] += count
            entrypoint["statuses"][status] = count
        models = [
            {
                "provider": str(row["provider"]),
                "model": str(row["model"]),
                "invocations": int(row["invocations"]),
                "input_tokens": int(row["input_tokens"]),
                "output_tokens": int(row["output_tokens"]),
                "billed_input_tokens": int(row["billed_input_tokens"]),
                "billed_output_tokens": int(row["billed_output_tokens"]),
                "missing_usage_invocations": int(row["missing_usage_invocations"]),
                "partial_usage_invocations": int(row["partial_usage_invocations"]),
                "missing_billing_invocations": int(row["missing_billing_invocations"]),
                "cost_usd": float(row["cost_usd"]),
            }
            for row in model_rows
        ]
        totals = {
            "runs": sum(statuses.values()),
            "model_invocations": sum(item["invocations"] for item in models),
            "input_tokens": sum(item["input_tokens"] for item in models),
            "output_tokens": sum(item["output_tokens"] for item in models),
            "billed_input_tokens": sum(item["billed_input_tokens"] for item in models),
            "billed_output_tokens": sum(item["billed_output_tokens"] for item in models),
            "missing_usage_invocations": sum(
                item["missing_usage_invocations"] for item in models
            ),
            "partial_usage_invocations": sum(
                item["partial_usage_invocations"] for item in models
            ),
            "missing_billing_invocations": sum(
                item["missing_billing_invocations"] for item in models
            ),
            "model_cost_usd": sum(item["cost_usd"] for item in models),
        }
        totals["usage_status"] = (
            "missing"
            if totals["model_invocations"]
            and totals["missing_usage_invocations"] >= totals["model_invocations"]
            else "partial"
            if totals["missing_usage_invocations"] or totals["partial_usage_invocations"]
            else "exact"
        )
        totals["billing_status"] = (
            "missing"
            if totals["model_invocations"]
            and totals["missing_billing_invocations"] >= totals["model_invocations"]
            else "partial"
            if totals["missing_billing_invocations"]
            else "exact"
        )
        meter_values = {
            "run.created": totals["runs"],
            **{f"run.{status}": count for status, count in statuses.items()},
            "model.invocation": totals["model_invocations"],
            "model.input_tokens": totals["input_tokens"],
            "model.output_tokens": totals["output_tokens"],
            "model.billed_input_tokens": totals["billed_input_tokens"],
            "model.billed_output_tokens": totals["billed_output_tokens"],
            "model.missing_usage_invocations": totals["missing_usage_invocations"],
            "model.missing_billing_invocations": totals[
                "missing_billing_invocations"
            ],
            "model.cost_usd": totals["model_cost_usd"],
        }
        declared = [
            {
                **dict(item),
                "supported": str(item.get("source_event") or "") in meter_values,
                "value": meter_values.get(str(item.get("source_event") or "")),
            }
            for item in dict(installation["manifest"]).get("metering") or []
        ]
        return {
            "installation_id": str(installation["installation_id"]),
            "app_id": str(installation["app_id"]),
            "version": str(installation["version"]),
            "period": {"since": since.isoformat(), "until": until.isoformat()},
            "totals": totals,
            "statuses": statuses,
            "entrypoints": list(entrypoints.values()),
            "models": models,
            "declared_meters": declared,
        }


__all__ = ["PostgresAppUsageStoreMixin"]
