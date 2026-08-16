"""PostgreSQL control plane for AgentTeams and shared Run Workspaces."""

from __future__ import annotations

from typing import Any

from porthouse.domain.agent_teams import AgentTeamRevision
from porthouse.storage.json_codec import Jsonb


class PostgresAgentTeamStoreMixin:
    def migrate_agent_teams(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS agent_team_definitions (
            team_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            current_revision_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK (status IN ('active','disabled','archived'))
        );
        CREATE TABLE IF NOT EXISTS agent_team_revisions (
            revision_id TEXT PRIMARY KEY,
            team_id TEXT NOT NULL REFERENCES agent_team_definitions(team_id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            definition JSONB NOT NULL,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            published_at TIMESTAMPTZ,
            UNIQUE(team_id,version),
            CHECK (status IN ('draft','published','retired'))
        );
        CREATE INDEX IF NOT EXISTS ix_agent_team_revisions_status
            ON agent_team_revisions(team_id,status,created_at DESC);
        CREATE TABLE IF NOT EXISTS agent_team_events (
            sequence BIGSERIAL PRIMARY KEY,
            team_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_agent_team_events_team
            ON agent_team_events(team_id,sequence DESC);
        CREATE TABLE IF NOT EXISTS team_workspace_entries (
            entry_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            root_run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            team_id TEXT NOT NULL,
            team_revision_id TEXT NOT NULL REFERENCES agent_team_revisions(revision_id),
            source_run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            source_task_id TEXT,
            member_id TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            visibility TEXT NOT NULL DEFAULT 'team',
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK (visibility IN ('team','coordinator'))
        );
        CREATE INDEX IF NOT EXISTS ix_team_workspace_scope
            ON team_workspace_entries(user_id,root_run_id,created_at,entry_id);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_team_workspace_task_entry
            ON team_workspace_entries(root_run_id,source_run_id,source_task_id,entry_type)
            WHERE source_task_id IS NOT NULL;
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="agent_teams",
                version=1,
                ddl=ddl,
                description="versioned AgentTeams and append-only Run Workspace",
            )

    def save_agent_team_revision(
        self, revision: AgentTeamRevision
    ) -> AgentTeamRevision:
        with self._pool.connection() as conn, conn.transaction():
            existing = conn.execute(
                "SELECT * FROM agent_team_revisions WHERE revision_id=%s FOR UPDATE",
                (revision.revision_id,),
            ).fetchone()
            if existing is not None and str(existing["status"]) != "draft":
                current = self._agent_team_revision(existing)
                if current.definition_dict() != revision.definition_dict():
                    raise ValueError("published AgentTeam revisions are immutable")
                return current
            conn.execute(
                """INSERT INTO agent_team_definitions(team_id,name,description)
                   VALUES (%s,%s,%s) ON CONFLICT(team_id) DO UPDATE SET
                     name=EXCLUDED.name,description=EXCLUDED.description,
                     updated_at=clock_timestamp()""",
                (revision.team_id, revision.name, revision.description),
            )
            conn.execute(
                """INSERT INTO agent_team_revisions
                       (revision_id,team_id,version,status,definition,created_by)
                   VALUES (%s,%s,%s,'draft',%s,%s)
                   ON CONFLICT(revision_id) DO UPDATE SET
                     definition=EXCLUDED.definition,created_by=EXCLUDED.created_by""",
                (
                    revision.revision_id,
                    revision.team_id,
                    revision.version,
                    Jsonb(revision.definition_dict()),
                    revision.created_by,
                ),
            )
            self._append_team_event(
                conn,
                team_id=revision.team_id,
                revision_id=revision.revision_id,
                event_type="draft_saved",
                actor_id=revision.created_by,
            )
        stored = self.get_agent_team_revision(revision.revision_id)
        assert stored is not None
        return stored

    def publish_agent_team_revision(
        self, team_id: str, revision_id: str, *, actor_id: str
    ) -> AgentTeamRevision:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT * FROM agent_team_revisions
                   WHERE team_id=%s AND revision_id=%s FOR UPDATE""",
                (team_id, revision_id),
            ).fetchone()
            if row is None:
                raise ValueError("AgentTeam revision not found")
            status = str(row["status"])
            if status not in {"draft", "published"}:
                raise ValueError("AgentTeam revision is not publishable")
            if status == "draft":
                conn.execute(
                    """UPDATE agent_team_revisions SET status='published',
                           published_at=clock_timestamp() WHERE revision_id=%s""",
                    (revision_id,),
                )
                conn.execute(
                    """UPDATE agent_team_definitions SET current_revision_id=%s,
                           updated_at=clock_timestamp() WHERE team_id=%s""",
                    (revision_id, team_id),
                )
                self._append_team_event(
                    conn,
                    team_id=team_id,
                    revision_id=revision_id,
                    event_type="published",
                    actor_id=actor_id,
                )
        stored = self.get_agent_team_revision(revision_id)
        assert stored is not None
        return stored

    def get_agent_team_revision(self, revision_id: str) -> AgentTeamRevision | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM agent_team_revisions WHERE revision_id=%s",
                (revision_id,),
            ).fetchone()
        return self._agent_team_revision(row) if row else None

    def get_published_agent_team(self, team_id: str) -> AgentTeamRevision | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT r.* FROM agent_team_definitions d
                   JOIN agent_team_revisions r ON r.revision_id=d.current_revision_id
                   WHERE d.team_id=%s AND d.status='active' AND r.status='published'""",
                (team_id,),
            ).fetchone()
        return self._agent_team_revision(row) if row else None

    def list_agent_team_revisions(
        self, team_id: str | None = None
    ) -> list[AgentTeamRevision]:
        with self._pool.connection() as conn:
            if team_id:
                rows = conn.execute(
                    """SELECT * FROM agent_team_revisions WHERE team_id=%s
                       ORDER BY version DESC,created_at DESC""",
                    (team_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT DISTINCT ON (r.team_id) r.*
                       FROM agent_team_revisions r
                       JOIN agent_team_definitions d USING(team_id)
                       ORDER BY r.team_id,
                         CASE WHEN r.revision_id=d.current_revision_id THEN 0 ELSE 1 END,
                         r.version DESC"""
                ).fetchall()
        return [self._agent_team_revision(row) for row in rows]

    def list_agent_team_events(
        self, team_id: str, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        from porthouse.storage.postgres_store import _iso

        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM agent_team_events WHERE team_id=%s
                   ORDER BY sequence DESC LIMIT %s""",
                (team_id, max(1, min(limit, 1000))),
            ).fetchall()
        return [
            {
                "sequence": int(row["sequence"]),
                "team_id": str(row["team_id"]),
                "revision_id": str(row["revision_id"]),
                "event_type": str(row["event_type"]),
                "actor_id": str(row["actor_id"]),
                "details": dict(row["details"]),
                "created_at": _iso(row["created_at"]),
            }
            for row in rows
        ]

    def append_team_workspace_entry(
        self,
        *,
        entry_id: str,
        user_id: str,
        root_run_id: str,
        team_id: str,
        team_revision_id: str,
        source_run_id: str,
        source_task_id: str | None,
        member_id: str,
        entry_type: str,
        summary: str,
        data: dict[str, Any],
        visibility: str = "team",
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            row = self._append_team_workspace_entry_tx(
                conn,
                entry_id=entry_id,
                user_id=user_id,
                root_run_id=root_run_id,
                team_id=team_id,
                team_revision_id=team_revision_id,
                source_run_id=source_run_id,
                source_task_id=source_task_id,
                member_id=member_id,
                entry_type=entry_type,
                summary=summary,
                data=data,
                visibility=visibility,
            )
        return self._team_workspace_dict(row)

    def _append_team_workspace_entry_tx(
        self, conn: Any, **values: Any
    ) -> Any:
        root_run_id = str(values["root_run_id"])
        user_id = str(values["user_id"])
        source_run_id = str(values["source_run_id"])
        source_task_id = values.get("source_task_id")
        root = conn.execute(
            """SELECT run_id,user_id,options FROM runtime_runs
               WHERE run_id=%s AND user_id=%s""",
            (root_run_id, user_id),
        ).fetchone()
        source = conn.execute(
            """WITH RECURSIVE ancestry AS (
                   SELECT run_id,user_id,parent_run_id
                   FROM runtime_runs WHERE run_id=%s
                   UNION ALL
                   SELECT parent.run_id,parent.user_id,parent.parent_run_id
                   FROM runtime_runs AS parent
                   JOIN ancestry AS child ON parent.run_id=child.parent_run_id
               )
               SELECT
                   bool_or(user_id=%s) AS same_user,
                   bool_or(run_id=%s) AS inside_workspace
               FROM ancestry""",
            (source_run_id, user_id, root_run_id),
        ).fetchone()
        if root is None or source is None:
            raise ValueError("AgentTeam Workspace Run scope is unavailable")
        if not bool(source["same_user"]) or not bool(source["inside_workspace"]):
            raise PermissionError("AgentTeam Workspace source is outside the user Run scope")
        metadata = dict(dict(root["options"] or {}).get("metadata") or {})
        team_ref = metadata.get("team_ref")
        if not isinstance(team_ref, dict) or (
            str(team_ref.get("team_id") or "") != str(values["team_id"])
            or str(team_ref.get("revision_id") or "")
            != str(values["team_revision_id"])
        ):
            raise ValueError("AgentTeam Workspace does not match the frozen root Run")
        member_ids = {
            str(item.get("member_id") or "")
            for item in metadata.get("team_members") or ()
            if isinstance(item, dict)
        }
        if str(values["member_id"]) not in member_ids:
            raise PermissionError("AgentTeam Workspace writer is outside the frozen Team")
        context_policy = dict(metadata.get("team_context_policy") or {})
        allowed_types = set(
            context_policy.get("workspace_entry_types")
            or ("task_result", "subagent_result")
        )
        if str(values["entry_type"]) not in allowed_types:
            raise ValueError("AgentTeam Workspace entry type is not allowed by policy")
        if str(values.get("visibility") or "team") not in {"team", "coordinator"}:
            raise ValueError("AgentTeam Workspace visibility is invalid")
        if source_task_id:
            task = conn.execute(
                "SELECT 1 FROM runtime_tasks WHERE task_id=%s AND run_id=%s",
                (source_task_id, source_run_id),
            ).fetchone()
            if task is None:
                raise ValueError("AgentTeam Workspace source Task is unavailable")
        conn.execute(
            """INSERT INTO team_workspace_entries
                   (entry_id,user_id,root_run_id,team_id,team_revision_id,source_run_id,
                    source_task_id,member_id,entry_type,summary,data,visibility)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING""",
            (
                str(values["entry_id"]),
                user_id,
                root_run_id,
                str(values["team_id"]),
                str(values["team_revision_id"]),
                source_run_id,
                source_task_id,
                str(values["member_id"]),
                str(values["entry_type"]),
                str(values.get("summary") or "")[:2000],
                Jsonb(dict(values.get("data") or {})),
                str(values.get("visibility") or "team"),
            ),
        )
        row = conn.execute(
            """SELECT * FROM team_workspace_entries
               WHERE root_run_id=%s AND source_run_id=%s
                 AND source_task_id IS NOT DISTINCT FROM %s AND entry_type=%s
               ORDER BY created_at DESC LIMIT 1""",
            (root_run_id, source_run_id, source_task_id, str(values["entry_type"])),
        ).fetchone()
        assert row is not None
        return row

    def list_team_workspace_entries(
        self,
        *,
        user_id: str,
        root_run_id: str,
        reader_member_id: str,
        coordinator: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM team_workspace_entries
                   WHERE user_id=%s AND root_run_id=%s
                     AND (visibility='team' OR member_id=%s OR %s)
                   ORDER BY created_at DESC,entry_id DESC LIMIT %s""",
                (
                    user_id,
                    root_run_id,
                    reader_member_id,
                    bool(coordinator),
                    max(1, min(limit, 200)),
                ),
            ).fetchall()
        return [self._team_workspace_dict(row) for row in reversed(rows)]

    @staticmethod
    def _append_team_event(
        conn: Any,
        *,
        team_id: str,
        revision_id: str,
        event_type: str,
        actor_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO agent_team_events
                   (team_id,revision_id,event_type,actor_id,details)
               VALUES (%s,%s,%s,%s,%s)""",
            (team_id, revision_id, event_type, actor_id, Jsonb(details or {})),
        )

    @staticmethod
    def _agent_team_revision(row: Any) -> AgentTeamRevision:
        from porthouse.storage.postgres_store import _iso

        value = dict(row["definition"])
        value.update(
            {
                "team_id": str(row["team_id"]),
                "revision_id": str(row["revision_id"]),
                "version": int(row["version"]),
                "status": str(row["status"]),
                "created_by": str(row["created_by"]),
                "created_at": _iso(row["created_at"]),
                "published_at": _iso(row["published_at"]),
            }
        )
        return AgentTeamRevision.from_dict(value)

    @staticmethod
    def _team_workspace_dict(row: Any) -> dict[str, Any]:
        from porthouse.storage.postgres_store import _iso

        return {
            "entry_id": str(row["entry_id"]),
            "user_id": str(row["user_id"]),
            "root_run_id": str(row["root_run_id"]),
            "team_id": str(row["team_id"]),
            "team_revision_id": str(row["team_revision_id"]),
            "source_run_id": str(row["source_run_id"]),
            "source_task_id": (
                str(row["source_task_id"]) if row["source_task_id"] else None
            ),
            "member_id": str(row["member_id"]),
            "entry_type": str(row["entry_type"]),
            "summary": str(row["summary"]),
            "data": dict(row["data"]),
            "visibility": str(row["visibility"]),
            "created_at": _iso(row["created_at"]),
        }
