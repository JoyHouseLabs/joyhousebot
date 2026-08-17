"""PostgreSQL plan confirmations for Team planning Runs.

A plan confirmation gates one Run's frozen Coordinator plan: the Worker
persists the plan preview (plan + compiled graph spec artifacts) and parks the
Run in ``waiting_input`` until the owner confirms, regenerates with feedback,
or cancels. Confirmation is deliberately a separate mechanism from Tool
Approvals and from field-shaped ``run_input_requests``; both are audited
independently.
"""

from __future__ import annotations

from typing import Any

from porthouse.storage.json_codec import Jsonb

_ACTION_STATUSES = {
    "confirm": "confirmed",
    "regenerate": "regenerate_requested",
    "cancel": "cancelled",
}
_MAX_PLAN_GENERATIONS = 5


class PostgresPlanConfirmationStoreMixin:
    def migrate_plan_confirmations(self) -> None:
        ddl = """
CREATE TABLE IF NOT EXISTS run_plan_confirmations (
  run_id TEXT PRIMARY KEY REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  team_id TEXT NOT NULL,
  team_revision_id TEXT NOT NULL,
  plan_version INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN
    ('awaiting_confirmation','confirmed','regenerate_requested','cancelled','expired','superseded')),
  plan_artifact_id TEXT NOT NULL,
  graph_spec_artifact_id TEXT NOT NULL,
  feedback TEXT,
  requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  action_at TIMESTAMPTZ,
  action_by TEXT,
  expires_at TIMESTAMPTZ NOT NULL DEFAULT (clock_timestamp()+interval '7 days')
);
CREATE INDEX IF NOT EXISTS ix_run_plan_confirmations_user
  ON run_plan_confirmations(user_id, requested_at DESC);
"""
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="plan_confirmations",
                version=1,
                ddl=ddl,
                description="owner confirmation gate for frozen Team coordinator plans",
            )

    def create_plan_confirmation(
        self,
        *,
        run_id: str,
        user_id: str,
        team_id: str,
        team_revision_id: str,
        plan_version: int,
        plan_artifact_id: str,
        graph_spec_artifact_id: str,
        expires_in_seconds: int = 7 * 86400,
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO run_plan_confirmations
                       (run_id,user_id,team_id,team_revision_id,plan_version,status,
                        plan_artifact_id,graph_spec_artifact_id,expires_at)
                   VALUES (%s,%s,%s,%s,%s,'awaiting_confirmation',%s,%s,
                           clock_timestamp()+(%s||' seconds')::interval)
                   ON CONFLICT (run_id) DO UPDATE SET
                     team_id=EXCLUDED.team_id,
                     team_revision_id=EXCLUDED.team_revision_id,
                     plan_version=EXCLUDED.plan_version,
                     status='awaiting_confirmation',
                     plan_artifact_id=EXCLUDED.plan_artifact_id,
                     graph_spec_artifact_id=EXCLUDED.graph_spec_artifact_id,
                     feedback=NULL,
                     requested_at=clock_timestamp(),
                     action_at=NULL,
                     action_by=NULL,
                     expires_at=EXCLUDED.expires_at
                   RETURNING *""",
                (
                    run_id,
                    user_id,
                    team_id,
                    team_revision_id,
                    int(plan_version),
                    plan_artifact_id,
                    graph_spec_artifact_id,
                    str(max(60, int(expires_in_seconds))),
                ),
            ).fetchone()
        return self._plan_confirmation(row)

    def get_plan_confirmation(self, run_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM run_plan_confirmations WHERE run_id=%s",
                (run_id,),
            ).fetchone()
        return self._plan_confirmation(row) if row else None

    def act_plan_confirmation(
        self,
        *,
        run_id: str,
        user_id: str,
        action: str,
        feedback: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically resolve an awaiting confirmation; None when not awaiting."""
        if action not in _ACTION_STATUSES:
            raise ValueError("plan confirmation action must be confirm, regenerate or cancel")
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE run_plan_confirmations SET status=%s,feedback=%s,
                       action_at=clock_timestamp(),action_by=%s
                   WHERE run_id=%s AND user_id=%s AND status='awaiting_confirmation'
                   RETURNING *""",
                (_ACTION_STATUSES[action], feedback, user_id, run_id, user_id),
            ).fetchone()
        return self._plan_confirmation(row) if row else None

    def queue_plan_confirmed_run(self, run_id: str) -> bool:
        """Move a confirmed Run back to the claimable queue (fenced)."""
        with self._pool.connection() as conn, conn.transaction():
            changed = conn.execute(
                """UPDATE runtime_runs SET status='queued',waiting_on=NULL,
                       updated_at=clock_timestamp()
                   WHERE run_id=%s AND status='waiting_input'""",
                (run_id,),
            ).rowcount
        if changed:
            self.notify_work(run_id)
        return bool(changed)

    def requeue_plan_regeneration(
        self, run_id: str, *, feedback: str
    ) -> dict[str, Any] | None:
        """Bump the plan generation, merge feedback, and requeue for planning."""
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE runtime_runs SET status='queued',waiting_on=NULL,
                       updated_at=clock_timestamp(),
                       options=jsonb_set(
                         jsonb_set(
                           options,
                           '{metadata,plan_generation}',
                           to_jsonb(COALESCE((options#>>'{metadata,plan_generation}')::int,0)+1),
                           true),
                         '{metadata,plan_regeneration}',
                         %s::jsonb, true)
                   WHERE run_id=%s AND status='waiting_input'
                   RETURNING options#>>'{metadata,plan_generation}' AS generation""",
                (Jsonb({"feedback": feedback[:4000], "at": "regenerate"}), run_id),
            ).fetchone()
        if row is not None:
            self.notify_work(run_id)
            return {"generation": int(row["generation"] or 1)}
        return None

    def expire_plan_confirmations(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Fail Runs whose confirmation window elapsed; fail-closed sweep."""
        expired: list[dict[str, Any]] = []
        with self._pool.connection() as conn, conn.transaction():
            rows = conn.execute(
                """SELECT confirmation.* FROM run_plan_confirmations AS confirmation
                   JOIN runtime_runs AS run ON run.run_id=confirmation.run_id
                   WHERE confirmation.status='awaiting_confirmation'
                     AND confirmation.expires_at < clock_timestamp()
                     AND run.status='waiting_input'
                   ORDER BY confirmation.expires_at
                   LIMIT %s FOR UPDATE OF confirmation,run SKIP LOCKED""",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """UPDATE run_plan_confirmations SET status='expired',
                           action_at=clock_timestamp(),action_by='system:expiry'
                       WHERE run_id=%s""",
                    (str(row["run_id"]),),
                )
                conn.execute(
                    """UPDATE runtime_runs SET status='failed',
                           waiting_on=NULL,updated_at=clock_timestamp(),
                           finished_at=clock_timestamp(),
                           error=%s::jsonb,
                           status_reason='plan_confirmation_expired'
                       WHERE run_id=%s AND status='waiting_input'""",
                    (
                        Jsonb({"code": "plan_confirmation_expired"}),
                        str(row["run_id"]),
                    ),
                )
                expired.append(self._plan_confirmation(row))
        return expired

    @staticmethod
    def _plan_confirmation(row: Any) -> dict[str, Any]:
        from porthouse.storage.postgres_store import _iso

        feedback = row["feedback"]
        action_by = row["action_by"]
        return {
            "run_id": str(row["run_id"]),
            "user_id": str(row["user_id"]),
            "team_id": str(row["team_id"]),
            "team_revision_id": str(row["team_revision_id"]),
            "plan_version": int(row["plan_version"]),
            "status": str(row["status"]),
            "plan_artifact_id": str(row["plan_artifact_id"]),
            "graph_spec_artifact_id": str(row["graph_spec_artifact_id"]),
            # feedback/action_by are TEXT columns, not JSONB.
            "feedback": str(feedback) if feedback is not None else None,
            "requested_at": _iso(row["requested_at"]),
            "action_at": _iso(row["action_at"]),
            "action_by": str(action_by) if action_by is not None else None,
            "expires_at": _iso(row["expires_at"]),
        }
