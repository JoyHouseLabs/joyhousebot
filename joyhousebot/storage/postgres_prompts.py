"""PostgreSQL persistence for first-class Prompt revisions and bindings."""

from __future__ import annotations

from typing import Any

from joyhousebot.domain.prompts import (
    bindable_prompt_content,
    normalize_prompt_document,
    prompt_content_sha256,
    validate_prompt_document,
)
from joyhousebot.storage.json_codec import Jsonb


class PostgresPromptStoreMixin:
    def migrate_prompts(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS prompt_definitions (
            prompt_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            current_revision_id TEXT,
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK (status IN ('active','disabled','archived'))
        );
        CREATE TABLE IF NOT EXISTS prompt_revisions (
            revision_id TEXT PRIMARY KEY,
            prompt_id TEXT NOT NULL REFERENCES prompt_definitions(prompt_id)
                ON DELETE CASCADE,
            version INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            content TEXT NOT NULL,
            input_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
            output_contract JSONB NOT NULL DEFAULT '{}'::jsonb,
            change_note TEXT NOT NULL DEFAULT '',
            content_sha256 TEXT NOT NULL,
            validation_report JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            validated_at TIMESTAMPTZ,
            published_at TIMESTAMPTZ,
            UNIQUE(prompt_id, version),
            CHECK (status IN ('draft','published','retired'))
        );
        CREATE INDEX IF NOT EXISTS ix_prompt_revisions_status
            ON prompt_revisions(prompt_id,status,version DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_prompt_one_published
            ON prompt_revisions(prompt_id) WHERE status='published';
        CREATE TABLE IF NOT EXISTS prompt_bindings (
            binding_id TEXT PRIMARY KEY,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            target_revision_id TEXT NOT NULL,
            prompt_revision_id TEXT NOT NULL REFERENCES prompt_revisions(revision_id),
            purpose TEXT NOT NULL DEFAULT 'system_instruction',
            position INTEGER NOT NULL DEFAULT 100,
            status TEXT NOT NULL DEFAULT 'active',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(target_type,target_revision_id,prompt_revision_id,purpose),
            CHECK (target_type IN ('agent')),
            CHECK (purpose IN ('system_instruction')),
            CHECK (status IN ('active','disabled'))
        );
        CREATE INDEX IF NOT EXISTS ix_prompt_bindings_target
            ON prompt_bindings(target_type,target_id,target_revision_id,position)
            WHERE status='active';
        CREATE TABLE IF NOT EXISTS prompt_events (
            sequence BIGSERIAL PRIMARY KEY,
            prompt_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_prompt_events_revision
            ON prompt_events(revision_id,sequence DESC);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341966,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="prompts",
                version=1,
                ddl=ddl,
                description="immutable Prompt assets, releases, bindings, and audit events",
            )

    def save_prompt_draft(self, value: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        document = normalize_prompt_document(value)
        digest = prompt_content_sha256(document)
        with self._pool.connection() as conn, conn.transaction():
            definition = conn.execute(
                "SELECT status FROM prompt_definitions WHERE prompt_id=%s FOR UPDATE",
                (document["prompt_id"],),
            ).fetchone()
            if definition is None:
                conn.execute(
                    """INSERT INTO prompt_definitions(prompt_id,name,description,tags)
                       VALUES (%s,%s,%s,%s)""",
                    (
                        document["prompt_id"],
                        document["name"],
                        document["description"],
                        Jsonb(document["tags"]),
                    ),
                )
            else:
                if str(definition["status"]) == "archived":
                    raise ValueError("archived Prompt cannot be edited")
                conn.execute(
                    """UPDATE prompt_definitions SET name=%s,description=%s,tags=%s,
                           updated_at=clock_timestamp() WHERE prompt_id=%s""",
                    (
                        document["name"],
                        document["description"],
                        Jsonb(document["tags"]),
                        document["prompt_id"],
                    ),
                )
            existing = conn.execute(
                """SELECT status FROM prompt_revisions
                   WHERE revision_id=%s FOR UPDATE""",
                (document["revision_id"],),
            ).fetchone()
            if existing is not None and str(existing["status"]) != "draft":
                raise ValueError("published or retired Prompt revisions are immutable")
            fields = (
                document["content"],
                Jsonb(document["input_schema"]),
                Jsonb(document["output_contract"]),
                document["change_note"],
                digest,
            )
            if existing is None:
                conn.execute(
                    """INSERT INTO prompt_revisions
                           (revision_id,prompt_id,version,content,input_schema,output_contract,
                            change_note,content_sha256,created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        document["revision_id"],
                        document["prompt_id"],
                        document["version"],
                        *fields,
                        actor_id,
                    ),
                )
                event_type = "draft.created"
            else:
                conn.execute(
                    """UPDATE prompt_revisions SET content=%s,input_schema=%s,
                           output_contract=%s,change_note=%s,content_sha256=%s,
                           validation_report='{}'::jsonb,validated_at=NULL,
                           updated_at=clock_timestamp() WHERE revision_id=%s""",
                    (*fields, document["revision_id"]),
                )
                event_type = "draft.updated"
            self._prompt_event(
                conn,
                prompt_id=document["prompt_id"],
                revision_id=document["revision_id"],
                event_type=event_type,
                actor_id=actor_id,
            )
        result = self.get_prompt_revision(document["revision_id"])
        assert result is not None
        return result

    def validate_prompt_revision(
        self, prompt_id: str, version: int, *, actor_id: str | None = None
    ) -> dict[str, Any]:
        revision = self.get_prompt_revision_by_version(prompt_id, version)
        if revision is None:
            raise ValueError("Prompt revision not found")
        report = validate_prompt_document(revision)
        if actor_id is not None:
            with self._pool.connection() as conn, conn.transaction():
                changed = conn.execute(
                    """UPDATE prompt_revisions SET validation_report=%s,
                           validated_at=clock_timestamp(),updated_at=clock_timestamp()
                       WHERE revision_id=%s AND status='draft'""",
                    (Jsonb(report), revision["revision_id"]),
                ).rowcount
                if changed:
                    self._prompt_event(
                        conn,
                        prompt_id=revision["prompt_id"],
                        revision_id=revision["revision_id"],
                        event_type="validation.passed" if report["valid"] else "validation.failed",
                        actor_id=actor_id,
                        data={"errors": list(report.get("errors") or [])},
                    )
        return report

    def publish_prompt_revision(
        self, prompt_id: str, version: int, *, actor_id: str
    ) -> dict[str, Any]:
        report = self.validate_prompt_revision(prompt_id, version, actor_id=actor_id)
        if not report["valid"]:
            raise ValueError("Prompt validation failed: " + "; ".join(report["errors"]))
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT revision_id,status FROM prompt_revisions
                   WHERE prompt_id=%s AND version=%s FOR UPDATE""",
                (prompt_id, version),
            ).fetchone()
            definition = conn.execute(
                "SELECT status FROM prompt_definitions WHERE prompt_id=%s FOR UPDATE",
                (prompt_id,),
            ).fetchone()
            if row is None or definition is None:
                raise ValueError("Prompt revision not found")
            if str(definition["status"]) != "active":
                raise ValueError("Prompt must be active before publication")
            if str(row["status"]) not in {"draft", "retired"}:
                raise ValueError("Prompt revision is already published")
            conn.execute(
                """UPDATE prompt_revisions SET status='retired',updated_at=clock_timestamp()
                   WHERE prompt_id=%s AND status='published'""",
                (prompt_id,),
            )
            conn.execute(
                """UPDATE prompt_revisions SET status='published',published_at=clock_timestamp(),
                           validation_report=%s,validated_at=clock_timestamp(),
                           updated_at=clock_timestamp() WHERE revision_id=%s""",
                (Jsonb(report), row["revision_id"]),
            )
            conn.execute(
                """UPDATE prompt_definitions SET current_revision_id=%s,
                           updated_at=clock_timestamp() WHERE prompt_id=%s""",
                (row["revision_id"], prompt_id),
            )
            self._prompt_event(
                conn,
                prompt_id=prompt_id,
                revision_id=str(row["revision_id"]),
                event_type="published",
                actor_id=actor_id,
                data={"validation_report": report},
            )
            self._notify(conn, f"config:prompt:{prompt_id}")
        result = self.get_prompt_revision_by_version(prompt_id, version)
        assert result is not None
        return result

    def list_prompts(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT d.*,r.version AS current_version_number,r.status AS current_status,
                          r.content_sha256 AS current_content_sha256,r.published_at
                   FROM prompt_definitions d
                   LEFT JOIN prompt_revisions r ON r.revision_id=d.current_revision_id
                   ORDER BY d.name,d.prompt_id"""
            ).fetchall()
        from joyhousebot.storage.postgres_store import _iso

        return [
            {
                "prompt_id": str(row["prompt_id"]),
                "name": str(row["name"]),
                "description": str(row["description"]),
                "status": str(row["status"]),
                "tags": list(row["tags"] or []),
                "current_revision_id": row["current_revision_id"],
                "current": (
                    {
                        "revision_id": str(row["current_revision_id"]),
                        "version": int(row["current_version_number"]),
                        "status": str(row["current_status"]),
                        "content_sha256": str(row["current_content_sha256"]),
                        "published_at": _iso(row["published_at"]),
                    }
                    if row["current_revision_id"]
                    else None
                ),
                "created_at": _iso(row["created_at"]),
                "updated_at": _iso(row["updated_at"]),
            }
            for row in rows
        ]

    def get_prompt(self, prompt_id: str) -> dict[str, Any] | None:
        for value in self.list_prompts():
            if value["prompt_id"] == prompt_id:
                return value
        return None

    def get_prompt_revision(self, revision_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT r.*,d.name,d.description,d.tags FROM prompt_revisions r
                   JOIN prompt_definitions d ON d.prompt_id=r.prompt_id
                   WHERE r.revision_id=%s""",
                (revision_id,),
            ).fetchone()
        return self._prompt_revision(row) if row else None

    def get_prompt_revision_by_version(self, prompt_id: str, version: int) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT r.*,d.name,d.description,d.tags FROM prompt_revisions r
                   JOIN prompt_definitions d ON d.prompt_id=r.prompt_id
                   WHERE r.prompt_id=%s AND r.version=%s""",
                (prompt_id, version),
            ).fetchone()
        return self._prompt_revision(row) if row else None

    def list_prompt_revisions(self, prompt_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT r.*,d.name,d.description,d.tags FROM prompt_revisions r
                   JOIN prompt_definitions d ON d.prompt_id=r.prompt_id
                   WHERE r.prompt_id=%s ORDER BY r.version DESC""",
                (prompt_id,),
            ).fetchall()
        return [self._prompt_revision(row) for row in rows]

    def bind_prompt_revision(
        self,
        *,
        binding_id: str,
        target_type: str,
        target_id: str,
        target_revision_id: str,
        prompt_revision_id: str,
        purpose: str,
        position: int,
        enabled: bool,
        actor_id: str,
    ) -> dict[str, Any]:
        if target_type != "agent" or purpose != "system_instruction":
            raise ValueError("only agent system_instruction Prompt bindings are supported")
        prompt = self.get_prompt_revision(prompt_revision_id)
        if prompt is None or prompt["status"] != "published":
            raise ValueError("only published Prompt revisions can be bound")
        bindable_prompt_content(prompt)
        with self._pool.connection() as conn, conn.transaction():
            agent = conn.execute(
                """SELECT 1 FROM agent_revisions
                   WHERE revision_id=%s AND agent_id=%s AND status='published'""",
                (target_revision_id, target_id),
            ).fetchone()
            if agent is None:
                raise ValueError("Prompt binding target must be a published Agent revision")
            conn.execute(
                """INSERT INTO prompt_bindings
                       (binding_id,target_type,target_id,target_revision_id,prompt_revision_id,
                        purpose,position,status,created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(target_type,target_revision_id,prompt_revision_id,purpose)
                   DO UPDATE SET binding_id=EXCLUDED.binding_id,position=EXCLUDED.position,
                       status=EXCLUDED.status,updated_at=clock_timestamp()""",
                (
                    binding_id,
                    target_type,
                    target_id,
                    target_revision_id,
                    prompt_revision_id,
                    purpose,
                    position,
                    "active" if enabled else "disabled",
                    actor_id,
                ),
            )
            self._prompt_event(
                conn,
                prompt_id=prompt["prompt_id"],
                revision_id=prompt_revision_id,
                event_type="binding.updated",
                actor_id=actor_id,
                data={
                    "binding_id": binding_id,
                    "target_type": target_type,
                    "target_id": target_id,
                    "target_revision_id": target_revision_id,
                    "enabled": enabled,
                },
            )
        return self.get_prompt_binding(target_type, target_revision_id, prompt_revision_id, purpose) or {}

    def get_prompt_binding(
        self, target_type: str, target_revision_id: str, prompt_revision_id: str, purpose: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM prompt_bindings WHERE target_type=%s AND target_revision_id=%s
                   AND prompt_revision_id=%s AND purpose=%s""",
                (target_type, target_revision_id, prompt_revision_id, purpose),
            ).fetchone()
        return self._prompt_binding(row) if row else None

    def list_active_prompt_bindings(
        self, *, target_type: str, target_id: str, target_revision_id: str
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT b.binding_id,b.target_type,b.target_id,b.target_revision_id,
                          b.prompt_revision_id,b.purpose,b.position,r.prompt_id,r.version,
                          r.content,r.content_sha256
                   FROM prompt_bindings b JOIN prompt_revisions r
                     ON r.revision_id=b.prompt_revision_id
                   WHERE b.target_type=%s AND b.target_id=%s AND b.target_revision_id=%s
                     AND b.status='active' AND r.status='published'
                   ORDER BY b.position,b.binding_id""",
                (target_type, target_id, target_revision_id),
            ).fetchall()
        return [
            {
                "binding_id": str(row["binding_id"]),
                "prompt_id": str(row["prompt_id"]),
                "revision_id": str(row["prompt_revision_id"]),
                "version": int(row["version"]),
                "purpose": str(row["purpose"]),
                "position": int(row["position"]),
                "content": str(row["content"]),
                "content_sha256": str(row["content_sha256"]),
            }
            for row in rows
        ]

    @staticmethod
    def _prompt_event(
        conn: Any,
        *,
        prompt_id: str,
        revision_id: str,
        event_type: str,
        actor_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO prompt_events(prompt_id,revision_id,event_type,actor_id,data)
               VALUES (%s,%s,%s,%s,%s)""",
            (prompt_id, revision_id, event_type, actor_id, Jsonb(data or {})),
        )

    @staticmethod
    def _prompt_revision(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "prompt_id": str(row["prompt_id"]),
            "revision_id": str(row["revision_id"]),
            "version": int(row["version"]),
            "name": str(row["name"]),
            "description": str(row["description"]),
            "tags": list(row["tags"] or []),
            "status": str(row["status"]),
            "content": str(row["content"]),
            "input_schema": dict(row["input_schema"] or {}),
            "output_contract": dict(row["output_contract"] or {}),
            "change_note": str(row["change_note"] or ""),
            "content_sha256": str(row["content_sha256"]),
            "validation_report": dict(row["validation_report"] or {}),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "validated_at": _iso(row["validated_at"]),
            "published_at": _iso(row["published_at"]),
        }

    @staticmethod
    def _prompt_binding(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "binding_id": str(row["binding_id"]),
            "target_type": str(row["target_type"]),
            "target_id": str(row["target_id"]),
            "target_revision_id": str(row["target_revision_id"]),
            "prompt_revision_id": str(row["prompt_revision_id"]),
            "purpose": str(row["purpose"]),
            "position": int(row["position"]),
            "status": str(row["status"]),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }
