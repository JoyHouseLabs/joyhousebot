"""User-owned, versioned Workflow definitions for Workflow Studio."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from porthouse.storage.json_codec import Jsonb


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


class PostgresWorkflowStoreMixin:
    def migrate_user_workflows(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS user_workflows (
            workflow_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            current_revision_id TEXT,
            published_revision_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_user_workflows_owner
            ON user_workflows(user_id, updated_at DESC);
        CREATE TABLE IF NOT EXISTS user_workflow_revisions (
            revision_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL REFERENCES user_workflows(workflow_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            goal TEXT NOT NULL,
            graph JSONB NOT NULL,
            change_note TEXT NOT NULL DEFAULT '',
            source_run_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            published_at TIMESTAMPTZ,
            UNIQUE(workflow_id, version)
        );
        CREATE INDEX IF NOT EXISTS ix_user_workflow_revisions_owner
            ON user_workflow_revisions(user_id, workflow_id, version DESC);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="user_workflows",
                version=1,
                ddl=ddl,
                description="user-owned Workflow definitions and immutable revisions",
            )

    @staticmethod
    def _workflow_revision(row: Any) -> dict[str, Any]:
        return {
            "revision_id": str(row["revision_id"]),
            "workflow_id": str(row["workflow_id"]),
            "version": int(row["version"]),
            "status": str(row["revision_status"] if "revision_status" in row else row["status"]),
            "goal": str(row["goal"]),
            "graph": dict(row["graph"]),
            "change_note": str(row["change_note"] or ""),
            "source_run_id": row["source_run_id"],
            "created_at": _iso(row["revision_created_at"] if "revision_created_at" in row else row["created_at"]),
            "published_at": _iso(row["published_at"]),
        }

    @classmethod
    def _workflow(cls, row: Any) -> dict[str, Any]:
        result = {
            "workflow_id": str(row["workflow_id"]),
            "user_id": str(row["user_id"]),
            "name": str(row["name"]),
            "description": str(row["description"] or ""),
            "status": str(row["status"]),
            "current_revision_id": row["current_revision_id"],
            "published_revision_id": row["published_revision_id"],
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }
        if row.get("revision_id"):
            result["revision"] = cls._workflow_revision(row)
        return result

    def create_user_workflow(self, **values: Any) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO user_workflows
                       (workflow_id,user_id,name,description,status,current_revision_id)
                   VALUES (%s,%s,%s,%s,'draft',%s)""",
                (
                    values["workflow_id"], values["user_id"], values["name"],
                    values.get("description", ""), values["revision_id"],
                ),
            )
            conn.execute(
                """INSERT INTO user_workflow_revisions
                       (revision_id,workflow_id,user_id,version,status,goal,graph,
                        change_note,source_run_id)
                   VALUES (%s,%s,%s,1,'draft',%s,%s,%s,%s)""",
                (
                    values["revision_id"], values["workflow_id"], values["user_id"],
                    values["goal"], Jsonb(values["graph"]), values.get("change_note", ""),
                    values.get("source_run_id"),
                ),
            )
        return self.get_user_workflow(
            values["workflow_id"], expected_user_id=values["user_id"]
        )

    def list_user_workflows(self, *, user_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT w.*,r.revision_id,r.version,r.status AS revision_status,
                          r.goal,r.graph,r.change_note,r.source_run_id,
                          r.created_at AS revision_created_at,r.published_at
                   FROM user_workflows w
                   LEFT JOIN user_workflow_revisions r
                     ON r.revision_id=w.current_revision_id
                   WHERE w.user_id=%s ORDER BY w.updated_at DESC""",
                (user_id,),
            ).fetchall()
        return [self._workflow(row) for row in rows]

    def get_user_workflow(
        self, workflow_id: str, *, expected_user_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT w.*,r.revision_id,r.version,r.status AS revision_status,
                          r.goal,r.graph,r.change_note,r.source_run_id,
                          r.created_at AS revision_created_at,r.published_at
                   FROM user_workflows w
                   LEFT JOIN user_workflow_revisions r
                     ON r.revision_id=w.current_revision_id
                   WHERE w.workflow_id=%s AND w.user_id=%s""",
                (workflow_id, expected_user_id),
            ).fetchone()
        return self._workflow(row) if row else None

    def create_user_workflow_revision(self, **values: Any) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            workflow = conn.execute(
                "SELECT * FROM user_workflows WHERE workflow_id=%s AND user_id=%s FOR UPDATE",
                (values["workflow_id"], values["user_id"]),
            ).fetchone()
            if workflow is None:
                return None
            version = int(
                conn.execute(
                    "SELECT COALESCE(max(version),0)+1 AS value FROM user_workflow_revisions WHERE workflow_id=%s",
                    (values["workflow_id"],),
                ).fetchone()["value"]
            )
            conn.execute(
                """INSERT INTO user_workflow_revisions
                       (revision_id,workflow_id,user_id,version,status,goal,graph,
                        change_note,source_run_id)
                   VALUES (%s,%s,%s,%s,'draft',%s,%s,%s,%s)""",
                (
                    values["revision_id"], values["workflow_id"], values["user_id"], version,
                    values["goal"], Jsonb(values["graph"]), values.get("change_note", ""),
                    values.get("source_run_id"),
                ),
            )
            conn.execute(
                """UPDATE user_workflows SET name=%s,description=%s,status='draft',
                          current_revision_id=%s,updated_at=clock_timestamp()
                   WHERE workflow_id=%s AND user_id=%s""",
                (
                    values["name"], values.get("description", ""), values["revision_id"],
                    values["workflow_id"], values["user_id"],
                ),
            )
        return self.get_user_workflow(
            values["workflow_id"], expected_user_id=values["user_id"]
        )

    def list_user_workflow_revisions(
        self, workflow_id: str, *, user_id: str
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM user_workflow_revisions
                   WHERE workflow_id=%s AND user_id=%s ORDER BY version DESC""",
                (workflow_id, user_id),
            ).fetchall()
        return [self._workflow_revision(row) for row in rows]

    def publish_user_workflow(
        self, workflow_id: str, revision_id: str, *, user_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            revision = conn.execute(
                """SELECT * FROM user_workflow_revisions
                   WHERE revision_id=%s AND workflow_id=%s AND user_id=%s FOR UPDATE""",
                (revision_id, workflow_id, user_id),
            ).fetchone()
            if revision is None:
                return None
            conn.execute(
                """UPDATE user_workflow_revisions SET status='superseded'
                   WHERE workflow_id=%s AND user_id=%s AND status='published'
                     AND revision_id<>%s""",
                (workflow_id, user_id, revision_id),
            )
            conn.execute(
                """UPDATE user_workflow_revisions
                   SET status='published',published_at=COALESCE(published_at,clock_timestamp())
                   WHERE revision_id=%s""",
                (revision_id,),
            )
            conn.execute(
                """UPDATE user_workflows SET status='published',current_revision_id=%s,
                          published_revision_id=%s,updated_at=clock_timestamp()
                   WHERE workflow_id=%s AND user_id=%s""",
                (revision_id, revision_id, workflow_id, user_id),
            )
        return self.get_user_workflow(workflow_id, expected_user_id=user_id)

    def delete_user_workflow(self, workflow_id: str, *, user_id: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            return conn.execute(
                "DELETE FROM user_workflows WHERE workflow_id=%s AND user_id=%s",
                (workflow_id, user_id),
            ).rowcount == 1
