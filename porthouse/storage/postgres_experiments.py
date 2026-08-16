"""Durable online experiment assignments for published Agent revisions."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from porthouse.storage.json_codec import Jsonb

_STATUSES = {"draft", "running", "paused", "stopped"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_bucket(*parts: str) -> int:
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % 10_000


class PostgresExperimentStoreMixin:
    def migrate_experiments(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS runtime_experiments (
            experiment_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            target_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            traffic_basis_points INTEGER NOT NULL DEFAULT 0,
            assignment_salt TEXT NOT NULL,
            variants JSONB NOT NULL,
            guardrails JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            started_at TIMESTAMPTZ,
            stopped_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK (target_type IN ('agent')),
            CHECK (status IN ('draft','running','paused','stopped')),
            CHECK (traffic_basis_points BETWEEN 0 AND 10000)
        );
        CREATE TABLE IF NOT EXISTS runtime_experiment_assignments (
            run_id TEXT PRIMARY KEY REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            experiment_id TEXT NOT NULL REFERENCES runtime_experiments(experiment_id)
                ON DELETE RESTRICT,
            subject_hash TEXT NOT NULL,
            assignment_bucket INTEGER NOT NULL,
            variant_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            target_revision_id TEXT NOT NULL,
            assigned_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK (assignment_bucket BETWEEN 0 AND 9999)
        );
        CREATE INDEX IF NOT EXISTS ix_runtime_experiment_assignments_experiment
            ON runtime_experiment_assignments(experiment_id,variant_id,assigned_at DESC);
        CREATE TABLE IF NOT EXISTS runtime_experiment_events (
            sequence BIGSERIAL PRIMARY KEY,
            experiment_id TEXT NOT NULL REFERENCES runtime_experiments(experiment_id)
                ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_runtime_experiment_events
            ON runtime_experiment_events(experiment_id,sequence DESC);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341967,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="experiments",
                version=1,
                ddl=ddl,
                description="stable online experiment assignment and guardrail evidence",
            )

    def save_experiment_draft(self, value: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        document = self._normalize_experiment(value)
        with self._pool.connection() as conn, conn.transaction():
            existing = conn.execute(
                "SELECT status,assignment_salt FROM runtime_experiments WHERE experiment_id=%s FOR UPDATE",
                (document["experiment_id"],),
            ).fetchone()
            if existing is not None and str(existing["status"]) in {"running", "stopped"}:
                raise ValueError("running or stopped experiments are immutable; create a new experiment")
            salt = (
                str(existing["assignment_salt"])
                if existing is not None
                # The assignment salt must not be derivable from the public
                # experiment identifier: it is used both for deterministic
                # bucketing and for the non-reversible stored subject hash.
                else f"exp:{secrets.token_urlsafe(32)}"
            )
            conn.execute(
                """INSERT INTO runtime_experiments
                       (experiment_id,name,description,target_type,status,traffic_basis_points,
                        assignment_salt,variants,guardrails,created_by)
                   VALUES (%s,%s,%s,%s,'draft',%s,%s,%s,%s,%s)
                   ON CONFLICT(experiment_id) DO UPDATE SET
                       name=EXCLUDED.name,description=EXCLUDED.description,
                       traffic_basis_points=EXCLUDED.traffic_basis_points,
                       variants=EXCLUDED.variants,guardrails=EXCLUDED.guardrails,
                       updated_at=clock_timestamp()""",
                (
                    document["experiment_id"],
                    document["name"],
                    document["description"],
                    document["target_type"],
                    document["traffic_basis_points"],
                    salt,
                    Jsonb(document["variants"]),
                    Jsonb(document["guardrails"]),
                    actor_id,
                ),
            )
            self._experiment_event(
                conn,
                experiment_id=document["experiment_id"],
                event_type="draft.created" if existing is None else "draft.updated",
                actor_id=actor_id,
                data={"document_sha256": hashlib.sha256(_canonical_json(document).encode()).hexdigest()},
            )
        result = self.get_experiment(document["experiment_id"])
        assert result is not None
        return result

    def start_experiment(self, experiment_id: str, *, actor_id: str) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT * FROM runtime_experiments WHERE experiment_id=%s FOR UPDATE",
                (experiment_id,),
            ).fetchone()
            if row is None:
                raise ValueError("experiment not found")
            document = self._experiment(row)
            if document["status"] == "stopped":
                raise ValueError("stopped experiment cannot be restarted")
            if document["traffic_basis_points"] <= 0:
                raise ValueError("experiment traffic must be greater than zero")
            self._assert_published_agent_variants(conn, document["variants"])
            conn.execute(
                """UPDATE runtime_experiments SET status='running',
                       started_at=COALESCE(started_at,clock_timestamp()),
                       updated_at=clock_timestamp() WHERE experiment_id=%s""",
                (experiment_id,),
            )
            self._experiment_event(
                conn, experiment_id=experiment_id, event_type="started", actor_id=actor_id
            )
        result = self.get_experiment(experiment_id)
        assert result is not None
        return result

    def set_experiment_status(
        self, experiment_id: str, *, status: str, actor_id: str, reason: str = ""
    ) -> dict[str, Any]:
        if status not in _STATUSES - {"draft"}:
            raise ValueError("experiment status must be running, paused, or stopped")
        if status == "running":
            return self.start_experiment(experiment_id, actor_id=actor_id)
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT status FROM runtime_experiments WHERE experiment_id=%s FOR UPDATE",
                (experiment_id,),
            ).fetchone()
            if row is None:
                raise ValueError("experiment not found")
            if str(row["status"]) == "stopped":
                raise ValueError("stopped experiment cannot be changed")
            conn.execute(
                """UPDATE runtime_experiments SET status=%s,
                       stopped_at=CASE WHEN %s='stopped' THEN clock_timestamp() ELSE stopped_at END,
                       updated_at=clock_timestamp() WHERE experiment_id=%s""",
                (status, status, experiment_id),
            )
            self._experiment_event(
                conn,
                experiment_id=experiment_id,
                event_type=f"status.{status}",
                actor_id=actor_id,
                data={"reason": reason[:2000]},
            )
        result = self.get_experiment(experiment_id)
        assert result is not None
        return result

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_experiments WHERE experiment_id=%s", (experiment_id,)
            ).fetchone()
        return self._experiment(row) if row else None

    def list_experiments(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime_experiments ORDER BY created_at DESC,experiment_id"
            ).fetchall()
        return [self._experiment(row) for row in rows]

    def select_experiment_variant(
        self, *, experiment_id: str, subject_id: str, target_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_experiments WHERE experiment_id=%s", (experiment_id,)
            ).fetchone()
        if row is None:
            raise ValueError("experiment not found")
        experiment = self._experiment(row, include_assignment_salt=True)
        if experiment["status"] != "running" or experiment["target_type"] != "agent":
            return None
        if not any(item["target_id"] == target_id for item in experiment["variants"]):
            raise ValueError("experiment does not apply to the selected Agent")
        bucket = _stable_bucket(experiment["assignment_salt"], subject_id, "eligible")
        if bucket >= experiment["traffic_basis_points"]:
            return None
        choice = _stable_bucket(experiment["assignment_salt"], subject_id, "variant")
        cursor = 0
        selected = experiment["variants"][-1]
        for variant in experiment["variants"]:
            cursor += int(variant["weight_basis_points"])
            if choice < cursor:
                selected = variant
                break
        return {
            "experiment_id": experiment_id,
            "variant_id": selected["variant_id"],
            "target_id": selected["target_id"],
            "target_revision_id": selected["target_revision_id"],
            "assignment_bucket": bucket,
        }

    def record_experiment_assignment(
        self, *, run_id: str, user_id: str, assignment: dict[str, Any]
    ) -> None:
        experiment_id = str(assignment.get("experiment_id") or "")
        if not experiment_id:
            return
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_experiments WHERE experiment_id=%s", (experiment_id,)
            ).fetchone()
        if row is None:
            raise ValueError("experiment not found while recording assignment")
        experiment = self._experiment(row, include_assignment_salt=True)
        subject_hash = hashlib.sha256(
            f"{experiment['assignment_salt']}:{user_id}".encode("utf-8")
        ).hexdigest()
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO runtime_experiment_assignments
                       (run_id,experiment_id,subject_hash,assignment_bucket,variant_id,
                        target_id,target_revision_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(run_id) DO NOTHING""",
                (
                    run_id,
                    experiment_id,
                    subject_hash,
                    int(assignment["assignment_bucket"]),
                    str(assignment["variant_id"]),
                    str(assignment["target_id"]),
                    str(assignment["target_revision_id"]),
                ),
            )

    def experiment_summary(self, experiment_id: str) -> dict[str, Any]:
        experiment = self.get_experiment(experiment_id)
        if experiment is None:
            raise ValueError("experiment not found")
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT a.variant_id,count(*) AS assigned,
                          count(*) FILTER (WHERE r.status='completed') AS completed,
                          count(*) FILTER (WHERE r.status IN ('failed','cancelled','timed_out')) AS failed,
                          avg(EXTRACT(EPOCH FROM (r.finished_at-r.created_at))*1000)
                              FILTER (WHERE r.finished_at IS NOT NULL) AS avg_latency_ms,
                          avg(COALESCE((r.result->'usage'->>'cost_usd')::double precision,0))
                              FILTER (WHERE r.status='completed') AS avg_cost_usd
                   FROM runtime_experiment_assignments a
                   JOIN runtime_runs r ON r.run_id=a.run_id
                   WHERE a.experiment_id=%s GROUP BY a.variant_id ORDER BY a.variant_id""",
                (experiment_id,),
            ).fetchall()
        variants = [
            {
                "variant_id": str(row["variant_id"]),
                "assigned": int(row["assigned"]),
                "completed": int(row["completed"]),
                "failed": int(row["failed"]),
                "failure_rate": (
                    int(row["failed"]) / int(row["assigned"])
                    if int(row["assigned"])
                    else 0.0
                ),
                "avg_latency_ms": float(row["avg_latency_ms"] or 0.0),
                "avg_cost_usd": float(row["avg_cost_usd"] or 0.0),
            }
            for row in rows
        ]
        return {"experiment": experiment, "variants": variants}

    def enforce_experiment_guardrails(self, experiment_id: str) -> dict[str, Any]:
        summary = self.experiment_summary(experiment_id)
        experiment = summary["experiment"]
        if experiment["status"] != "running":
            return {"paused": False, "summary": summary}
        guardrails = dict(experiment["guardrails"] or {})
        minimum = int(guardrails.get("min_assigned") or 20)
        max_failure = guardrails.get("max_failure_rate")
        max_latency = guardrails.get("max_avg_latency_ms")
        max_cost = guardrails.get("max_avg_cost_usd")
        violations: list[dict[str, Any]] = []
        for variant in summary["variants"]:
            if variant["assigned"] < minimum:
                continue
            if max_failure is not None and variant["failure_rate"] > float(max_failure):
                violations.append({"variant_id": variant["variant_id"], "metric": "failure_rate"})
            if max_latency is not None and variant["avg_latency_ms"] > float(max_latency):
                violations.append({"variant_id": variant["variant_id"], "metric": "avg_latency_ms"})
            if max_cost is not None and variant["avg_cost_usd"] > float(max_cost):
                violations.append({"variant_id": variant["variant_id"], "metric": "avg_cost_usd"})
        if not violations:
            return {"paused": False, "summary": summary}
        self.set_experiment_status(
            experiment_id,
            status="paused",
            actor_id="runtime.guardrail",
            reason="guardrail: " + _canonical_json(violations),
        )
        return {"paused": True, "violations": violations, "summary": summary}

    @staticmethod
    def _normalize_experiment(value: dict[str, Any]) -> dict[str, Any]:
        experiment_id = str(value.get("experiment_id") or "").strip()
        name = str(value.get("name") or "").strip()
        if not experiment_id or len(experiment_id) > 128 or not name or len(name) > 160:
            raise ValueError("experiment_id and name are required")
        target_type = str(value.get("target_type") or "agent")
        if target_type != "agent":
            raise ValueError("only Agent revision experiments are currently supported")
        traffic = int(value.get("traffic_basis_points") or 0)
        if not 0 <= traffic <= 10_000:
            raise ValueError("traffic_basis_points must be between 0 and 10000")
        variants = [dict(item) for item in list(value.get("variants") or [])]
        if not 2 <= len(variants) <= 16:
            raise ValueError("experiment requires 2-16 Agent revision variants")
        seen: set[str] = set()
        total = 0
        normalized: list[dict[str, Any]] = []
        for item in variants:
            variant_id = str(item.get("variant_id") or "").strip()
            target_id = str(item.get("target_id") or "").strip()
            revision_id = str(item.get("target_revision_id") or "").strip()
            weight = int(item.get("weight_basis_points") or 0)
            if not variant_id or variant_id in seen or not target_id or not revision_id or weight <= 0:
                raise ValueError("each experiment variant requires unique id, target and positive weight")
            seen.add(variant_id)
            total += weight
            normalized.append(
                {
                    "variant_id": variant_id,
                    "target_id": target_id,
                    "target_revision_id": revision_id,
                    "weight_basis_points": weight,
                }
            )
        if total != 10_000:
            raise ValueError("experiment variant weights must total 10000 basis points")
        guardrails = dict(value.get("guardrails") or {})
        for key in ("max_failure_rate",):
            if key in guardrails and not 0 <= float(guardrails[key]) <= 1:
                raise ValueError(f"{key} must be between 0 and 1")
        for key in ("max_avg_latency_ms", "max_avg_cost_usd", "min_assigned"):
            if key in guardrails and float(guardrails[key]) < 0:
                raise ValueError(f"{key} must be non-negative")
        return {
            "experiment_id": experiment_id,
            "name": name,
            "description": str(value.get("description") or "")[:2000],
            "target_type": target_type,
            "traffic_basis_points": traffic,
            "variants": normalized,
            "guardrails": guardrails,
        }

    @staticmethod
    def _assert_published_agent_variants(conn: Any, variants: list[dict[str, Any]]) -> None:
        for variant in variants:
            found = conn.execute(
                """SELECT 1 FROM agent_revisions WHERE revision_id=%s AND agent_id=%s
                   AND status='published'""",
                (variant["target_revision_id"], variant["target_id"]),
            ).fetchone()
            if found is None:
                raise ValueError(
                    "experiment variant must reference a published Agent revision: "
                    f"{variant['target_id']}@{variant['target_revision_id']}"
                )

    @staticmethod
    def _experiment_event(
        conn: Any,
        *,
        experiment_id: str,
        event_type: str,
        actor_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO runtime_experiment_events(experiment_id,event_type,actor_id,data)
               VALUES (%s,%s,%s,%s)""",
            (experiment_id, event_type, actor_id, Jsonb(data or {})),
        )

    @staticmethod
    def _experiment(row: Any, *, include_assignment_salt: bool = False) -> dict[str, Any]:
        from porthouse.storage.postgres_store import _iso

        value = {
            "experiment_id": str(row["experiment_id"]),
            "name": str(row["name"]),
            "description": str(row["description"]),
            "target_type": str(row["target_type"]),
            "status": str(row["status"]),
            "traffic_basis_points": int(row["traffic_basis_points"]),
            "variants": list(row["variants"] or []),
            "guardrails": dict(row["guardrails"] or {}),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "started_at": _iso(row["started_at"]),
            "stopped_at": _iso(row["stopped_at"]),
            "updated_at": _iso(row["updated_at"]),
        }
        if include_assignment_salt:
            value["assignment_salt"] = str(row["assignment_salt"])
        return value
