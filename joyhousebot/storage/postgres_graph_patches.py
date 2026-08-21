"""Atomic append/replace-pending GraphPatch persistence."""

from __future__ import annotations

from typing import Any

from joyhousebot.storage.graph_patch_records import (
    GraphPatchProposalRecord,
    GraphPatchRecord,
)
from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.runtime_store import RuntimeRunRecord

_TERMINAL_RUNS = ("completed", "failed", "cancelled", "timed_out")


class PostgresGraphPatchStoreMixin:
    def migrate_graph_patches(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS graph_patches (
            patch_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            base_revision_id TEXT NOT NULL REFERENCES graph_revisions(revision_id),
            result_revision_id TEXT NOT NULL UNIQUE REFERENCES graph_revisions(revision_id),
            proposer_type TEXT NOT NULL,
            proposer_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            operations JSONB NOT NULL,
            diff JSONB NOT NULL,
            validation JSONB NOT NULL,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'applied',
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_graph_patches_owner_run
            ON graph_patches(user_id, run_id, created_at, patch_id);

        CREATE OR REPLACE FUNCTION reject_graph_patch_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Graph patches are immutable';
        END;
        $$;
        DROP TRIGGER IF EXISTS trg_graph_patches_immutable ON graph_patches;
        CREATE TRIGGER trg_graph_patches_immutable BEFORE UPDATE ON graph_patches
            FOR EACH ROW EXECUTE FUNCTION reject_graph_patch_mutation();
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341930,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="graph_patches",
                version=1,
                ddl=ddl,
                description="immutable controlled GraphPatch audit ledger",
            )
            proposal_ddl = """
            CREATE TABLE IF NOT EXISTS graph_patch_proposals (
                proposal_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
                user_id TEXT NOT NULL,
                base_revision_id TEXT NOT NULL REFERENCES graph_revisions(revision_id),
                proposer_type TEXT NOT NULL,
                proposer_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                operations JSONB NOT NULL,
                diff JSONB NOT NULL,
                validation JSONB NOT NULL,
                request_hash TEXT NOT NULL,
                candidate_revision JSONB NOT NULL,
                task_rows JSONB NOT NULL,
                append_ids JSONB NOT NULL,
                replace_ids JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                applied_patch_id TEXT REFERENCES graph_patches(patch_id),
                resolution TEXT,
                note TEXT,
                resolved_by TEXT,
                error JSONB,
                lease_owner TEXT,
                lease_version BIGINT NOT NULL DEFAULT 0,
                lease_expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                resolved_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
                UNIQUE (user_id, run_id, request_hash),
                CHECK (status IN
                    ('pending','activating','approved','rejected','activation_failed'))
            );
            CREATE INDEX IF NOT EXISTS ix_graph_patch_proposals_owner_run
                ON graph_patch_proposals(user_id, run_id, created_at, proposal_id);
            CREATE INDEX IF NOT EXISTS ix_graph_patch_proposals_pending
                ON graph_patch_proposals(status, lease_expires_at, updated_at);
            """
            conn.execute(proposal_ddl)
            self._record_migration(
                conn,
                name="graph_patches",
                version=2,
                ddl=proposal_ddl,
                description="durable GraphPatch proposal and approval state machine",
            )

    def propose_graph_patch(
        self, *, proposal: dict[str, Any]
    ) -> tuple[GraphPatchProposalRecord, bool]:
        with self._pool.connection() as conn, conn.transaction():
            existing = conn.execute(
                "SELECT * FROM graph_patch_proposals WHERE proposal_id=%s",
                (proposal["proposal_id"],),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["run_id"]) != proposal["run_id"]
                    or str(existing["user_id"]) != proposal["user_id"]
                    or str(existing["request_hash"]) != proposal["request_hash"]
                ):
                    raise ValueError("GraphPatch proposal identity conflict")
                return self._graph_patch_proposal(existing), False
            run = conn.execute(
                "SELECT * FROM runtime_runs WHERE run_id=%s AND user_id=%s FOR UPDATE",
                (proposal["run_id"], proposal["user_id"]),
            ).fetchone()
            if run is None:
                raise ValueError("Graph Run not found")
            if str(run["kind"]) != "graph" or str(run["status"]) in _TERMINAL_RUNS:
                raise ValueError("GraphPatch proposal requires a non-terminal Graph Run")
            if str(run["graph_revision_id"] or "") != proposal["base_revision_id"]:
                raise ValueError("GraphPatch proposal base revision is stale")
            parent = conn.execute(
                "SELECT * FROM graph_revisions WHERE revision_id=%s AND run_id=%s FOR SHARE",
                (proposal["base_revision_id"], proposal["run_id"]),
            ).fetchone()
            if parent is None:
                raise ValueError("GraphPatch proposal base revision does not belong to Run")
            candidate = dict(proposal["candidate_revision"])
            if (
                candidate.get("parent_revision_id") != proposal["base_revision_id"]
                or int(candidate["revision_number"])
                != int(parent["revision_number"]) + 1
            ):
                raise ValueError("GraphPatch proposal revision lineage is invalid")
            row = conn.execute(
                """INSERT INTO graph_patch_proposals
                       (proposal_id,run_id,user_id,base_revision_id,proposer_type,
                        proposer_id,reason,operations,diff,validation,request_hash,
                        candidate_revision,task_rows,append_ids,replace_ids)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *""",
                (
                    proposal["proposal_id"],
                    proposal["run_id"],
                    proposal["user_id"],
                    proposal["base_revision_id"],
                    proposal["proposer_type"],
                    proposal["proposer_id"],
                    proposal["reason"],
                    Jsonb(proposal["operations"]),
                    Jsonb(proposal["diff"]),
                    Jsonb(proposal["validation"]),
                    proposal["request_hash"],
                    Jsonb(candidate),
                    Jsonb(proposal["task_rows"]),
                    Jsonb(proposal["append_ids"]),
                    Jsonb(proposal["replace_ids"]),
                ),
            ).fetchone()
            self._audit(
                conn,
                run_id=proposal["run_id"],
                stage="store.graph.patch_proposed",
                message="GraphPatch proposal is waiting for independent approval",
                data={
                    "proposal_id": proposal["proposal_id"],
                    "proposer_type": proposal["proposer_type"],
                    "risk": proposal["validation"].get("risk"),
                },
            )
            self._notify(conn, proposal["run_id"])
            assert row is not None
            return self._graph_patch_proposal(row), True

    def claim_graph_patch_proposal(
        self,
        proposal_id: str,
        *,
        expected_user_id: str,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> GraphPatchProposalRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE graph_patch_proposals SET status='activating',lease_owner=%s,
                       lease_version=lease_version+1,
                       lease_expires_at=clock_timestamp()+(%s*interval '1 second'),
                       updated_at=clock_timestamp()
                   WHERE proposal_id=%s AND user_id=%s AND
                       (status='pending' OR
                        (status='activating' AND lease_expires_at<clock_timestamp()))
                   RETURNING *""",
                (worker_id, max(1, lease_seconds), proposal_id, expected_user_id),
            ).fetchone()
        return self._graph_patch_proposal(row) if row else None

    def finish_graph_patch_proposal(
        self,
        proposal_id: str,
        *,
        expected_user_id: str,
        worker_id: str,
        lease_version: int,
        status: str,
        applied_patch_id: str | None = None,
        error: dict[str, Any] | None = None,
        resolved_by: str,
        note: str | None = None,
    ) -> GraphPatchProposalRecord | None:
        if status not in {"approved", "activation_failed"}:
            raise ValueError("invalid GraphPatch proposal terminal status")
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE graph_patch_proposals SET status=%s,applied_patch_id=%s,
                       resolution=%s,note=%s,resolved_by=%s,error=%s,
                       lease_owner=NULL,lease_expires_at=NULL,
                       resolved_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE proposal_id=%s AND user_id=%s AND status='activating'
                     AND lease_owner=%s AND lease_version=%s RETURNING *""",
                (
                    status,
                    applied_patch_id,
                    "approve" if status == "approved" else "activation_failed",
                    note,
                    resolved_by,
                    Jsonb(error) if error else None,
                    proposal_id,
                    expected_user_id,
                    worker_id,
                    lease_version,
                ),
            ).fetchone()
            if row:
                self._audit(
                    conn,
                    run_id=str(row["run_id"]),
                    stage=f"store.graph.patch_proposal.{status}",
                    message=f"GraphPatch proposal {status}",
                    level="error" if status == "activation_failed" else "info",
                    data={"proposal_id": proposal_id, "applied_patch_id": applied_patch_id},
                )
                self._notify(conn, str(row["run_id"]))
        return self._graph_patch_proposal(row) if row else None

    def reject_graph_patch_proposal(
        self,
        proposal_id: str,
        *,
        expected_user_id: str,
        resolved_by: str,
        note: str | None = None,
    ) -> GraphPatchProposalRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE graph_patch_proposals SET status='rejected',resolution='reject',
                       note=%s,resolved_by=%s,resolved_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE proposal_id=%s AND user_id=%s AND status='pending'
                   RETURNING *""",
                (note, resolved_by, proposal_id, expected_user_id),
            ).fetchone()
            if row:
                self._audit(
                    conn,
                    run_id=str(row["run_id"]),
                    stage="store.graph.patch_proposal.rejected",
                    message="GraphPatch proposal rejected",
                    data={"proposal_id": proposal_id},
                )
                self._notify(conn, str(row["run_id"]))
        return self._graph_patch_proposal(row) if row else None

    def get_graph_patch_proposal(
        self, proposal_id: str, *, expected_user_id: str
    ) -> GraphPatchProposalRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM graph_patch_proposals WHERE proposal_id=%s AND user_id=%s",
                (proposal_id, expected_user_id),
            ).fetchone()
        return self._graph_patch_proposal(row) if row else None

    def list_graph_patch_proposals(
        self, run_id: str, *, expected_user_id: str
    ) -> list[GraphPatchProposalRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM graph_patch_proposals WHERE run_id=%s AND user_id=%s
                   ORDER BY created_at,proposal_id""",
                (run_id, expected_user_id),
            ).fetchall()
        return [self._graph_patch_proposal(row) for row in rows]

    def apply_graph_patch(
        self,
        *,
        patch: dict[str, Any],
        revision: dict[str, Any],
        task_rows: list[dict[str, Any]],
        append_ids: list[str],
        replace_ids: list[str],
    ) -> tuple[GraphPatchRecord, RuntimeRunRecord, bool]:
        """Apply revision, Task mutations, Run pointer and audit as one commit."""
        with self._pool.connection() as conn, conn.transaction():
            existing = conn.execute(
                "SELECT * FROM graph_patches WHERE patch_id=%s",
                (patch["patch_id"],),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["run_id"]) != patch["run_id"]
                    or str(existing["user_id"]) != patch["user_id"]
                    or str(existing["request_hash"]) != patch["request_hash"]
                ):
                    raise ValueError("GraphPatch identity conflict")
                run = conn.execute(
                    "SELECT * FROM runtime_runs WHERE run_id=%s AND user_id=%s",
                    (patch["run_id"], patch["user_id"]),
                ).fetchone()
                assert run is not None
                return self._graph_patch(existing), self._run(run), False

            run = conn.execute(
                """SELECT *,lease_owner IS NOT NULL
                          AND lease_expires_at>=clock_timestamp() AS lease_live
                   FROM runtime_runs WHERE run_id=%s AND user_id=%s FOR UPDATE""",
                (patch["run_id"], patch["user_id"]),
            ).fetchone()
            if run is None:
                raise ValueError("Graph Run not found")
            if str(run["kind"]) != "graph" or str(run["status"]) in _TERMINAL_RUNS:
                raise ValueError("GraphPatch requires a non-terminal Graph Run")
            if bool(run["lease_live"]):
                raise ValueError("GraphPatch conflicts with active Graph finalization")
            if str(run["graph_revision_id"] or "") != patch["base_revision_id"]:
                raise ValueError("GraphPatch base revision is stale")
            parent = conn.execute(
                "SELECT * FROM graph_revisions WHERE revision_id=%s AND run_id=%s FOR SHARE",
                (patch["base_revision_id"], patch["run_id"]),
            ).fetchone()
            if parent is None:
                raise ValueError("GraphPatch base revision does not belong to Run")
            if (
                revision.get("parent_revision_id") != patch["base_revision_id"]
                or int(revision["revision_number"]) != int(parent["revision_number"]) + 1
            ):
                raise ValueError("GraphPatch revision lineage is invalid")

            runtime_tasks = conn.execute(
                "SELECT * FROM runtime_tasks WHERE run_id=%s FOR UPDATE",
                (patch["run_id"],),
            ).fetchall()
            top_level = {
                str((row["payload"] or {}).get("spec_id") or ""): row
                for row in runtime_tasks
                if row["parent_task_id"] is None
            }
            base_ids = {
                str(row["node_id"])
                for row in conn.execute(
                    "SELECT node_id FROM graph_revision_nodes WHERE revision_id=%s",
                    (patch["base_revision_id"],),
                ).fetchall()
            }
            final_ids = {str(node["node_id"]) for node in revision["nodes"]}
            if (
                set(append_ids) & base_ids
                or not set(replace_ids) <= base_ids
                or final_ids != base_ids | set(append_ids)
                or base_ids != set(top_level)
            ):
                raise ValueError("GraphPatch node set conflicts with materialized Run")
            for node_id in append_ids:
                if f"{patch['run_id']}:{node_id}" in {str(row["task_id"]) for row in runtime_tasks}:
                    raise ValueError(f"GraphPatch task identity already exists: {node_id}")
            self._lock_patch_affected_tasks(
                conn, patch["run_id"], [f"{patch['run_id']}:{item}" for item in replace_ids]
            )

            rows_by_id = {
                str(row["payload"]["spec_id"]): row
                for row in task_rows
                if str(row["payload"]["spec_id"]) in set(append_ids) | set(replace_ids)
            }
            if set(rows_by_id) != set(append_ids) | set(replace_ids):
                raise ValueError("GraphPatch materialization rows are incomplete")

            self._insert_graph_revision(
                conn,
                run_id=patch["run_id"],
                user_id=patch["user_id"],
                revision=revision,
                created_by=patch["proposer_id"],
            )
            self._replace_pending_patch_tasks(conn, patch["run_id"], replace_ids, rows_by_id)
            self._append_patch_tasks(conn, patch["run_id"], append_ids, rows_by_id)
            touched = [f"{patch['run_id']}:{item}" for item in [*replace_ids, *append_ids]]
            if touched:
                conn.execute(
                    """UPDATE runtime_tasks task SET status='queued',updated_at=clock_timestamp()
                       WHERE task.task_id=ANY(%s) AND task.status='blocked' AND NOT EXISTS (
                           SELECT 1 FROM runtime_task_dependencies dependency
                           JOIN runtime_tasks parent
                             ON parent.task_id=dependency.depends_on_task_id
                           WHERE dependency.task_id=task.task_id
                             AND parent.status!='completed')""",
                    (touched,),
                )
            options = dict(run["options"] or {})
            options.update(
                {
                    "graph_revision_id": revision["revision_id"],
                    "tasks": [dict(node) for node in revision["nodes"]],
                }
            )
            saved_run = conn.execute(
                """UPDATE runtime_runs SET graph_revision_id=%s,options=%s,
                          total_task_count=total_task_count+%s,
                          lease_owner=NULL,lease_expires_at=NULL,
                          lease_version=lease_version+1,updated_at=clock_timestamp()
                   WHERE run_id=%s RETURNING *""",
                (
                    revision["revision_id"],
                    Jsonb(options),
                    len(append_ids),
                    patch["run_id"],
                ),
            ).fetchone()
            saved_patch = conn.execute(
                """INSERT INTO graph_patches
                       (patch_id,run_id,user_id,base_revision_id,result_revision_id,
                        proposer_type,proposer_id,reason,operations,diff,validation,request_hash)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    patch["patch_id"],
                    patch["run_id"],
                    patch["user_id"],
                    patch["base_revision_id"],
                    revision["revision_id"],
                    patch["proposer_type"],
                    patch["proposer_id"],
                    patch["reason"],
                    Jsonb(patch["operations"]),
                    Jsonb(patch["diff"]),
                    Jsonb(patch["validation"]),
                    patch["request_hash"],
                ),
            ).fetchone()
            self._audit(
                conn,
                run_id=patch["run_id"],
                stage="store.graph.patched",
                message="Controlled GraphPatch committed atomically",
                data={
                    "patch_id": patch["patch_id"],
                    "base_revision_id": patch["base_revision_id"],
                    "result_revision_id": revision["revision_id"],
                    "added": append_ids,
                    "replaced": replace_ids,
                },
            )
            self._notify(conn, patch["run_id"])
            assert saved_patch is not None and saved_run is not None
            return self._graph_patch(saved_patch), self._run(saved_run), True

    @staticmethod
    def _lock_patch_affected_tasks(conn: Any, run_id: str, task_ids: list[str]) -> None:
        if not task_ids:
            return
        affected = conn.execute(
            """WITH RECURSIVE affected(task_id) AS (
                   SELECT unnest(%s::text[])
                   UNION
                   SELECT dependency.task_id
                   FROM runtime_task_dependencies dependency
                   JOIN affected ON affected.task_id=dependency.depends_on_task_id
               )
               SELECT task.* FROM runtime_tasks task
               WHERE task.run_id=%s AND task.task_id IN (SELECT task_id FROM affected)
               FOR UPDATE""",
            (task_ids, run_id),
        ).fetchall()
        found = {str(row["task_id"]) for row in affected}
        if not set(task_ids) <= found:
            raise ValueError("GraphPatch replacement target is unavailable")
        unsafe = [
            str(row["task_id"])
            for row in affected
            if str(row["status"]) not in {"queued", "blocked"}
            or int(row["attempt"]) != 0
            or row["started_at"] is not None
            or row["result"] is not None
            or row["error"] is not None
            or row["lease_owner"] is not None
        ]
        if unsafe:
            raise ValueError(
                "GraphPatch cannot replace a started node or affect started downstream nodes: "
                f"{sorted(unsafe)}"
            )

    @staticmethod
    def _replace_pending_patch_tasks(
        conn: Any,
        run_id: str,
        replace_ids: list[str],
        rows_by_id: dict[str, dict[str, Any]],
    ) -> None:
        for node_id in replace_ids:
            row = rows_by_id[node_id]
            task_id = f"{run_id}:{node_id}"
            conn.execute("DELETE FROM runtime_task_dependencies WHERE task_id=%s", (task_id,))
            conn.execute(
                """UPDATE runtime_tasks SET agent_id=%s,name=%s,status=%s,payload=%s,
                          priority=%s,max_attempts=%s,available_at=clock_timestamp(),
                          updated_at=clock_timestamp()
                   WHERE task_id=%s""",
                (
                    row["agent_id"],
                    row["name"],
                    row.get("initial_status")
                    or ("blocked" if row["dependencies"] else "queued"),
                    Jsonb(row["payload"]),
                    row["priority"],
                    max(1, int(row["max_attempts"])),
                    task_id,
                ),
            )
            PostgresGraphPatchStoreMixin._insert_patch_dependencies(conn, task_id, row)

    @staticmethod
    def _append_patch_tasks(
        conn: Any,
        run_id: str,
        append_ids: list[str],
        rows_by_id: dict[str, dict[str, Any]],
    ) -> None:
        for node_id in append_ids:
            row = rows_by_id[node_id]
            task_id = f"{run_id}:{node_id}"
            conn.execute(
                """INSERT INTO runtime_tasks
                       (task_id,run_id,agent_id,name,status,payload,priority,max_attempts)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    task_id,
                    run_id,
                    row["agent_id"],
                    row["name"],
                    row.get("initial_status")
                    or ("blocked" if row["dependencies"] else "queued"),
                    Jsonb(row["payload"]),
                    row["priority"],
                    max(1, int(row["max_attempts"])),
                ),
            )
            PostgresGraphPatchStoreMixin._insert_patch_dependencies(conn, task_id, row)

    @staticmethod
    def _insert_patch_dependencies(conn: Any, task_id: str, row: dict[str, Any]) -> None:
        if row["dependencies"]:
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO runtime_task_dependencies(task_id,depends_on_task_id)
                       VALUES (%s,%s)""",
                    [(task_id, dependency) for dependency in row["dependencies"]],
                )

    def list_graph_patches(self, run_id: str, *, expected_user_id: str) -> list[GraphPatchRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM graph_patches WHERE run_id=%s AND user_id=%s
                   ORDER BY created_at,patch_id""",
                (run_id, expected_user_id),
            ).fetchall()
        return [self._graph_patch(row) for row in rows]

    @staticmethod
    def _graph_patch(row: dict[str, Any]) -> GraphPatchRecord:
        from joyhousebot.storage.postgres_store import _iso, _json

        return GraphPatchRecord(
            patch_id=str(row["patch_id"]),
            run_id=str(row["run_id"]),
            user_id=str(row["user_id"]),
            base_revision_id=str(row["base_revision_id"]),
            result_revision_id=str(row["result_revision_id"]),
            proposer_type=str(row["proposer_type"]),
            proposer_id=str(row["proposer_id"]),
            reason=str(row["reason"]),
            operations=list(_json(row["operations"], [])),
            diff=dict(_json(row["diff"], {})),
            validation=dict(_json(row["validation"], {})),
            request_hash=str(row["request_hash"]),
            status=str(row["status"]),
            created_at=_iso(row["created_at"]) or "",
        )

    @staticmethod
    def _graph_patch_proposal(row: dict[str, Any]) -> GraphPatchProposalRecord:
        from joyhousebot.storage.postgres_store import _iso, _json

        return GraphPatchProposalRecord(
            proposal_id=str(row["proposal_id"]),
            run_id=str(row["run_id"]),
            user_id=str(row["user_id"]),
            base_revision_id=str(row["base_revision_id"]),
            proposer_type=str(row["proposer_type"]),
            proposer_id=str(row["proposer_id"]),
            reason=str(row["reason"]),
            operations=list(_json(row["operations"], [])),
            diff=dict(_json(row["diff"], {})),
            validation=dict(_json(row["validation"], {})),
            request_hash=str(row["request_hash"]),
            status=str(row["status"]),
            candidate_revision=dict(_json(row["candidate_revision"], {})),
            task_rows=list(_json(row["task_rows"], [])),
            append_ids=[str(item) for item in _json(row["append_ids"], [])],
            replace_ids=[str(item) for item in _json(row["replace_ids"], [])],
            applied_patch_id=(
                str(row["applied_patch_id"]) if row["applied_patch_id"] else None
            ),
            resolution=str(row["resolution"]) if row["resolution"] else None,
            note=str(row["note"]) if row["note"] else None,
            resolved_by=str(row["resolved_by"]) if row["resolved_by"] else None,
            error=dict(_json(row["error"], {})) or None,
            lease_owner=str(row["lease_owner"]) if row["lease_owner"] else None,
            lease_version=int(row["lease_version"]),
            created_at=_iso(row["created_at"]) or "",
            resolved_at=_iso(row["resolved_at"]),
        )
