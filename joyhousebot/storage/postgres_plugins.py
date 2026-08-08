"""PostgreSQL control-plane records for externally supplied plugins."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from joyhousebot.storage.json_codec import Jsonb


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


class PostgresPluginStoreMixin:
    """Durable plugin release catalog and operational projections."""

    def migrate_plugins(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS plugin_releases (
            plugin_id TEXT NOT NULL,
            version TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            distribution_name TEXT NOT NULL DEFAULT '',
            build_digest TEXT NOT NULL DEFAULT '',
            manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(plugin_id, version)
        );
        CREATE INDEX IF NOT EXISTS ix_plugin_releases_active
            ON plugin_releases(plugin_id, updated_at DESC) WHERE status='active';
        CREATE TABLE IF NOT EXISTS plugin_components (
            plugin_id TEXT NOT NULL,
            plugin_version TEXT NOT NULL,
            component_id TEXT NOT NULL,
            component_type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            reference_id TEXT NOT NULL DEFAULT '',
            reference_version TEXT NOT NULL DEFAULT '',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(plugin_id, plugin_version, component_id),
            FOREIGN KEY(plugin_id, plugin_version) REFERENCES plugin_releases(plugin_id, version)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_plugin_components_reference
            ON plugin_components(plugin_id, reference_id, reference_version);
        CREATE TABLE IF NOT EXISTS plugin_check_results (
            check_id BIGSERIAL PRIMARY KEY,
            plugin_id TEXT NOT NULL,
            plugin_version TEXT NOT NULL,
            check_name TEXT NOT NULL,
            status TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            worker_id TEXT,
            duration_ms INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_plugin_checks_latest
            ON plugin_check_results(plugin_id, check_name, created_at DESC);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341919,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="plugins",
                version=1,
                ddl=ddl,
                description="plugin release catalog and operational projections",
            )

    def upsert_plugin_release(self, manifest: dict[str, Any]) -> None:
        value = dict(manifest)
        plugin_id = str(value["plugin_id"])
        version = str(value["version"])
        build_digest = str(value.get("build_digest") or "").strip()
        if not build_digest:
            raise ValueError("plugin release build_digest is required")
        with self._pool.connection() as conn, conn.transaction():
            existing = conn.execute(
                """SELECT build_digest FROM plugin_releases
                   WHERE plugin_id=%s AND version=%s FOR UPDATE""",
                (plugin_id, version),
            ).fetchone()
            if existing is not None and str(existing["build_digest"]) != build_digest:
                raise ValueError(
                    "plugin release is immutable; publish a new version for a new build digest"
                )
            conn.execute(
                """INSERT INTO plugin_releases
                       (plugin_id,version,name,description,distribution_name,build_digest,manifest)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(plugin_id,version) DO UPDATE SET
                       name=EXCLUDED.name,description=EXCLUDED.description,
                       distribution_name=EXCLUDED.distribution_name,
                       build_digest=EXCLUDED.build_digest,manifest=EXCLUDED.manifest,
                       status='active',updated_at=clock_timestamp()""",
                (
                    plugin_id,
                    version,
                    str(value.get("name") or plugin_id),
                    str(value.get("description") or ""),
                    str(value.get("distribution_name") or ""),
                    build_digest,
                    Jsonb(value),
                ),
            )

    def sync_plugin_components(
        self,
        plugin_id: str,
        plugin_version: str,
        components: list[dict[str, Any]],
        *,
        replace: bool = False,
    ) -> None:
        with self._pool.connection() as conn, conn.transaction():
            component_ids = [str(item["component_id"]) for item in components]
            if replace:
                conn.execute(
                    "DELETE FROM plugin_components WHERE plugin_id=%s AND plugin_version=%s"
                    + (" AND NOT (component_id = ANY(%s))" if component_ids else ""),
                    (plugin_id, plugin_version, component_ids)
                    if component_ids
                    else (plugin_id, plugin_version),
                )
            for component in components:
                value = dict(component)
                conn.execute(
                    """INSERT INTO plugin_components
                           (plugin_id,plugin_version,component_id,component_type,name,description,
                            reference_id,reference_version,metadata)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(plugin_id,plugin_version,component_id) DO UPDATE SET
                           component_type=EXCLUDED.component_type,name=EXCLUDED.name,
                           description=EXCLUDED.description,reference_id=EXCLUDED.reference_id,
                           reference_version=EXCLUDED.reference_version,
                           metadata=EXCLUDED.metadata,updated_at=clock_timestamp()""",
                    (
                        plugin_id,
                        plugin_version,
                        str(value["component_id"]),
                        str(value["component_type"]),
                        str(value.get("name") or value["component_id"]),
                        str(value.get("description") or ""),
                        str(value.get("reference_id") or ""),
                        str(value.get("reference_version") or ""),
                        Jsonb(dict(value.get("metadata") or {})),
                    ),
                )

    def list_plugin_releases(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT ON (plugin_id) * FROM plugin_releases
                   WHERE status='active' ORDER BY plugin_id,updated_at DESC"""
            ).fetchall()
        return [self._release_dict(row) for row in rows]

    def get_plugin_release(
        self, plugin_id: str, version: str | None = None
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            if version:
                row = conn.execute(
                    """SELECT * FROM plugin_releases WHERE plugin_id=%s AND version=%s
                       AND status='active'""",
                    (plugin_id, version),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT * FROM plugin_releases WHERE plugin_id=%s AND status='active'
                       ORDER BY updated_at DESC LIMIT 1""",
                    (plugin_id,),
                ).fetchone()
        return self._release_dict(row) if row else None

    @staticmethod
    def _release_dict(row: Any) -> dict[str, Any]:
        return {
            "plugin_id": str(row["plugin_id"]), "version": str(row["version"]),
            "name": str(row["name"]), "description": str(row["description"]),
            "distribution_name": str(row["distribution_name"]),
            "build_digest": str(row["build_digest"]), "manifest": dict(_json(row["manifest"], {})),
            "status": str(row["status"]), "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }

    def list_plugin_components(self, plugin_id: str) -> list[dict[str, Any]]:
        release = self.get_plugin_release(plugin_id)
        if release is None:
            return []
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM plugin_components WHERE plugin_id=%s AND plugin_version=%s
                   ORDER BY component_type,name,component_id""",
                (plugin_id, release["version"]),
            ).fetchall()
        return [
            {"component_id": str(row["component_id"]), "component_type": str(row["component_type"]),
             "name": str(row["name"]), "description": str(row["description"]),
             "reference_id": str(row["reference_id"]), "reference_version": str(row["reference_version"]),
             "metadata": dict(_json(row["metadata"], {})), "plugin_id": plugin_id,
             "plugin_version": release["version"]}
            for row in rows
        ]

    def list_plugin_workers(self, plugin_id: str) -> list[dict[str, Any]]:
        release = self.get_plugin_release(plugin_id)
        values = []
        for worker in self.list_runtime_workers(limit=5000):
            plugin = next(
                (
                    item for item in worker["metadata"].get("plugins", [])
                    if item.get("plugin_id") == plugin_id
                ),
                None,
            )
            release_matched = bool(
                plugin and release
                and str(plugin.get("version") or "") == release["version"]
                and str(plugin.get("build_digest") or "") == release["build_digest"]
            )
            values.append({
                **worker,
                "plugin": plugin,
                "release_matched": release_matched,
                "execution_eligible": bool(worker.get("healthy")) and release_matched,
            })
        return values

    def record_plugin_check_result(
        self,
        plugin_id: str,
        plugin_version: str,
        check_name: str,
        status: str,
        summary: str,
        *,
        details: dict[str, Any] | None = None,
        worker_id: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """INSERT INTO plugin_check_results
                       (plugin_id,plugin_version,check_name,status,summary,details,worker_id,duration_ms)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (plugin_id, plugin_version, check_name, status, summary, Jsonb(details or {}), worker_id, duration_ms),
            )

    def list_plugin_check_results(self, plugin_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT ON (check_name) check_name,status,summary,details,worker_id,duration_ms,created_at
                   FROM plugin_check_results WHERE plugin_id=%s
                   ORDER BY check_name,created_at DESC LIMIT %s""",
                (plugin_id, max(1, min(200, limit))),
            ).fetchall()
        return [
            {"name": str(row["check_name"]), "status": str(row["status"]),
             "summary": str(row["summary"]), "details": dict(_json(row["details"], {})),
             "worker_id": row["worker_id"], "duration_ms": row["duration_ms"],
             "created_at": _iso(row["created_at"])}
            for row in rows
        ]

    def get_plugin_metrics(self, plugin_id: str, *, hours: int = 24) -> dict[str, Any]:
        components = self.list_plugin_components(plugin_id)
        references = [item["reference_id"] for item in components if item["component_type"] == "tool"]
        if not references:
            return {"hours": hours, "total": 0, "succeeded": 0, "failed": 0, "success_rate": 0.0,
                    "p50_duration_ms": None, "p95_duration_ms": None, "by_component": []}
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT capability_id,count(*) AS total,
                          count(*) FILTER (WHERE status='succeeded') AS succeeded,
                          count(*) FILTER (WHERE status IN ('failed','timed_out','cancelled')) AS failed,
                          percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (finished_at-started_at))*1000)
                              FILTER (WHERE finished_at IS NOT NULL AND started_at IS NOT NULL) AS p50,
                          percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (finished_at-started_at))*1000)
                              FILTER (WHERE finished_at IS NOT NULL AND started_at IS NOT NULL) AS p95
                   FROM capability_invocations
                   WHERE capability_id = ANY(%s) AND created_at >= clock_timestamp()-(%s * interval '1 hour')
                   GROUP BY capability_id ORDER BY total DESC""",
                (references, max(1, min(24 * 90, hours))),
            ).fetchall()
        total = sum(int(row["total"]) for row in rows)
        succeeded = sum(int(row["succeeded"]) for row in rows)
        failed = sum(int(row["failed"]) for row in rows)
        return {
            "hours": hours, "total": total, "succeeded": succeeded, "failed": failed,
            "success_rate": round(succeeded / total, 4) if total else 0.0,
            "p50_duration_ms": self._weighted_metric(rows, "p50", total),
            "p95_duration_ms": self._weighted_metric(rows, "p95", total),
            "by_component": [{"capability_id": str(row["capability_id"]), "total": int(row["total"]),
                              "succeeded": int(row["succeeded"]), "failed": int(row["failed"]),
                              "p50_duration_ms": round(float(row["p50"]), 1) if row["p50"] is not None else None,
                              "p95_duration_ms": round(float(row["p95"]), 1) if row["p95"] is not None else None}
                             for row in rows],
        }

    @staticmethod
    def _weighted_metric(rows: list[Any], key: str, total: int) -> float | None:
        values = [(float(row[key]), int(row["total"])) for row in rows if row[key] is not None]
        if not values or not total:
            return None
        return round(sum(value * count for value, count in values) / sum(count for _, count in values), 1)

    def list_plugin_recent_invocations(self, plugin_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        references = [item["reference_id"] for item in self.list_plugin_components(plugin_id)
                      if item["component_type"] == "tool"]
        if not references:
            return []
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT invocation_id,capability_id,capability_version,run_id,task_id,status,worker_id,
                          error,created_at,started_at,finished_at
                   FROM capability_invocations WHERE capability_id = ANY(%s)
                   ORDER BY created_at DESC LIMIT %s""", (references, max(1, min(500, limit)))
            ).fetchall()
        return [{key: (_iso(row[key]) if key.endswith("_at") else _json(row[key], None) if key == "error" else row[key])
                 for key in row.keys()} for row in rows]
