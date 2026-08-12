"""Read-only App installation usage and model-cost attribution."""

from __future__ import annotations

from datetime import datetime
from typing import Any


class PostgresAppUsageStoreMixin:
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
                          COALESCE(options#>>'{metadata,app,entrypoint_id}','unknown')
                            AS entrypoint_id,
                          COUNT(*) AS count
                   FROM runtime_runs
                   WHERE user_id=%s AND created_at>=%s AND created_at<%s
                     AND options#>>'{metadata,app,installation_id}'=%s
                   GROUP BY status,entrypoint_id
                   ORDER BY entrypoint_id,status""",
                (user_id, since, until, installation_id),
            ).fetchall()
            model_rows = conn.execute(
                """WITH app_roots AS (
                     SELECT run_id FROM runtime_runs
                     WHERE user_id=%s AND created_at>=%s AND created_at<%s
                       AND options#>>'{metadata,app,installation_id}'=%s
                   ), app_runs AS (
                     SELECT run_id FROM runtime_runs
                     WHERE root_run_id IN (SELECT run_id FROM app_roots)
                   )
                   SELECT provider,model,COUNT(*) AS invocations,
                          COALESCE(SUM((usage->>'input_tokens')::bigint),0) AS input_tokens,
                          COALESCE(SUM((usage->>'output_tokens')::bigint),0) AS output_tokens,
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
                "cost_usd": float(row["cost_usd"]),
            }
            for row in model_rows
        ]
        totals = {
            "runs": sum(statuses.values()),
            "model_invocations": sum(item["invocations"] for item in models),
            "input_tokens": sum(item["input_tokens"] for item in models),
            "output_tokens": sum(item["output_tokens"] for item in models),
            "model_cost_usd": sum(item["cost_usd"] for item in models),
        }
        meter_values = {
            "run.created": totals["runs"],
            **{f"run.{status}": count for status, count in statuses.items()},
            "model.invocation": totals["model_invocations"],
            "model.input_tokens": totals["input_tokens"],
            "model.output_tokens": totals["output_tokens"],
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
