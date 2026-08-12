"""PostgreSQL control plane for independent, versioned Skill assets."""

from __future__ import annotations

from typing import Any

from joyhousebot.domain.skills import (
    normalize_skill_document,
    skill_content_sha256,
    validate_skill_document,
)
from joyhousebot.storage.json_codec import Jsonb


class PostgresSkillStoreMixin:
    def migrate_skills(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS skill_definitions (
            skill_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            current_version TEXT,
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK (status IN ('active','disabled','archived'))
        );
        CREATE TABLE IF NOT EXISTS skill_versions (
            skill_id TEXT NOT NULL REFERENCES skill_definitions(skill_id)
                ON DELETE CASCADE,
            version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            instruction_content TEXT NOT NULL DEFAULT '',
            input_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
            output_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
            required_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
            required_integrations JSONB NOT NULL DEFAULT '[]'::jsonb,
            examples JSONB NOT NULL DEFAULT '[]'::jsonb,
            eval_cases JSONB NOT NULL DEFAULT '[]'::jsonb,
            templates JSONB NOT NULL DEFAULT '[]'::jsonb,
            change_note TEXT NOT NULL DEFAULT '',
            source JSONB NOT NULL DEFAULT '{}'::jsonb,
            content_sha256 TEXT NOT NULL,
            validation_report JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            validated_at TIMESTAMPTZ,
            published_at TIMESTAMPTZ,
            PRIMARY KEY(skill_id,version),
            CHECK (status IN ('draft','staged','published','retired'))
        );
        CREATE INDEX IF NOT EXISTS ix_skill_versions_status
            ON skill_versions(skill_id,status,created_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_one_published
            ON skill_versions(skill_id) WHERE status='published';
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="skills",
                version=1,
                ddl=ddl,
                description="independent immutable Skill assets and publication lifecycle",
            )
            current_pointer_ddl = """
            UPDATE skill_definitions definition
               SET current_version=version.version,updated_at=clock_timestamp()
              FROM skill_versions version
             WHERE version.skill_id=definition.skill_id
               AND version.status='published'
               AND definition.current_version IS DISTINCT FROM version.version;
            """
            conn.execute(current_pointer_ddl)
            self._record_migration(
                conn,
                name="skills",
                version=2,
                ddl=current_pointer_ddl,
                description="repair Skill current pointer from the unique published version",
            )
            conn.execute(
                """UPDATE agent_skill_bindings binding
                   SET skill_content_sha256=version.content_sha256
                   FROM skill_versions version
                   WHERE binding.skill_id=version.skill_id
                     AND binding.skill_version=version.version
                     AND binding.skill_content_sha256=''"""
            )

    def save_skill_draft(
        self,
        value: dict[str, Any],
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        document = normalize_skill_document(value)
        digest = skill_content_sha256(document)
        with self._pool.connection() as conn, conn.transaction():
            existing_definition = conn.execute(
                "SELECT status FROM skill_definitions WHERE skill_id=%s FOR UPDATE",
                (document["skill_id"],),
            ).fetchone()
            if existing_definition is None:
                conn.execute(
                    """INSERT INTO skill_definitions
                           (skill_id,name,description,tags)
                       VALUES (%s,%s,%s,%s)""",
                    (
                        document["skill_id"],
                        document["name"],
                        document["description"],
                        Jsonb(document["tags"]),
                    ),
                )
            else:
                if str(existing_definition["status"]) == "archived":
                    raise ValueError("archived Skill cannot be edited")
                conn.execute(
                    """UPDATE skill_definitions SET name=%s,description=%s,tags=%s,
                           updated_at=clock_timestamp() WHERE skill_id=%s""",
                    (
                        document["name"],
                        document["description"],
                        Jsonb(document["tags"]),
                        document["skill_id"],
                    ),
                )
            existing = conn.execute(
                """SELECT status FROM skill_versions
                   WHERE skill_id=%s AND version=%s FOR UPDATE""",
                (document["skill_id"], document["version"]),
            ).fetchone()
            if existing is not None and str(existing["status"]) != "draft":
                raise ValueError("published or staged Skill versions are immutable")
            fields = (
                document["instruction_content"],
                Jsonb(document["input_schema"]),
                Jsonb(document["output_schema"]),
                Jsonb(document["required_capabilities"]),
                Jsonb(document["required_integrations"]),
                Jsonb(document["examples"]),
                Jsonb(document["eval_cases"]),
                Jsonb(document["templates"]),
                document["change_note"],
                Jsonb(document["source"]),
                digest,
            )
            if existing is None:
                conn.execute(
                    """INSERT INTO skill_versions
                           (skill_id,version,status,instruction_content,input_schema,
                            output_schema,required_capabilities,required_integrations,
                            examples,eval_cases,templates,change_note,source,
                            content_sha256,created_by)
                       VALUES (%s,%s,'draft',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (document["skill_id"], document["version"], *fields, actor_id),
                )
                event_type = "draft.created"
            else:
                conn.execute(
                    """UPDATE skill_versions SET instruction_content=%s,input_schema=%s,
                           output_schema=%s,required_capabilities=%s,
                           required_integrations=%s,examples=%s,eval_cases=%s,
                           templates=%s,change_note=%s,source=%s,content_sha256=%s,
                           validation_report='{}'::jsonb,validated_at=NULL,
                           updated_at=clock_timestamp()
                       WHERE skill_id=%s AND version=%s""",
                    (*fields, document["skill_id"], document["version"]),
                )
                event_type = "draft.updated"
            self._append_configuration_event(
                conn,
                "skill",
                document["skill_id"],
                document["version"],
                event_type,
                actor_id,
            )
        result = self.get_skill_version(document["skill_id"], document["version"])
        assert result is not None
        return result

    def validate_skill_version(
        self,
        skill_id: str,
        version: str,
        *,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        value = self.get_skill_version(skill_id, version)
        if value is None:
            raise ValueError("Skill version not found")
        with self._pool.connection() as conn:
            capability_rows = conn.execute(
                """SELECT capability_id,version FROM capability_versions
                   WHERE status='published'"""
            ).fetchall()
            integration_rows = conn.execute(
                """SELECT c.connection_id FROM remote_connections c
                   JOIN remote_connection_revisions r
                     ON r.connection_id=c.connection_id
                    AND r.revision_id=c.current_revision_id
                   WHERE r.status='published'"""
            ).fetchall()
        report = validate_skill_document(
            value,
            available_capabilities={
                (str(row["capability_id"]), str(row["version"]))
                for row in capability_rows
            },
            available_integrations={str(row["connection_id"]) for row in integration_rows},
        )
        if actor_id is not None:
            with self._pool.connection() as conn, conn.transaction():
                changed = conn.execute(
                    """UPDATE skill_versions SET validation_report=%s,
                           validated_at=clock_timestamp(),updated_at=clock_timestamp()
                       WHERE skill_id=%s AND version=%s AND status='draft'""",
                    (Jsonb(report), skill_id, version),
                ).rowcount
                if changed:
                    self._append_configuration_event(
                        conn,
                        "skill",
                        skill_id,
                        version,
                        "validation.passed" if report["valid"] else "validation.failed",
                        actor_id,
                    )
        return report

    def stage_skill_version(
        self,
        skill_id: str,
        version: str,
        *,
        actor_id: str,
        activation_mode: str = "automatic",
        timeout_seconds: int = 300,
        auto_rollback: bool = True,
        require_healthy_workers: bool = True,
    ) -> str:
        report = self.validate_skill_version(skill_id, version, actor_id=actor_id)
        if not report["valid"]:
            raise ValueError("Skill validation failed: " + "; ".join(report["errors"]))
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT status,content_sha256 FROM skill_versions
                   WHERE skill_id=%s AND version=%s FOR UPDATE""",
                (skill_id, version),
            ).fetchone()
            definition = conn.execute(
                "SELECT status FROM skill_definitions WHERE skill_id=%s FOR UPDATE",
                (skill_id,),
            ).fetchone()
            if row is None or definition is None:
                raise ValueError("Skill version not found")
            if str(definition["status"]) != "active":
                raise ValueError("Skill must be active before publication")
            if str(row["status"]) not in {"draft", "staged", "retired"}:
                raise ValueError("Skill version is already published")
            conn.execute(
                """UPDATE skill_versions SET
                       status=CASE WHEN status='retired' THEN 'retired' ELSE 'staged' END,
                       validation_report=%s,
                       validated_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE skill_id=%s AND version=%s""",
                (Jsonb(report), skill_id, version),
            )
            rollout_id = self._create_configuration_rollout(
                conn,
                aggregate_type="skill",
                aggregate_id=skill_id,
                revision_id=version,
                actor_id=actor_id,
                activation_mode=activation_mode,
                timeout_seconds=timeout_seconds,
                auto_rollback=auto_rollback,
                require_healthy_workers=require_healthy_workers,
                target_worker_capability="agent",
            )
            self._append_configuration_event(
                conn, "skill", skill_id, version, "publish.requested", actor_id
            )
            self._notify(conn, f"config:skill:{skill_id}")
        return rollout_id

    def list_skills(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE d.status='active'" if active_only else ""
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"""SELECT d.*,
                           current.status AS current_status,
                           current.content_sha256 AS current_content_sha256,
                           current.published_at AS current_published_at,
                           latest.version AS latest_version,
                           latest.status AS latest_status,
                           latest.content_sha256 AS latest_content_sha256,
                           latest.updated_at AS latest_updated_at
                    FROM skill_definitions d
                    LEFT JOIN skill_versions current
                      ON current.skill_id=d.skill_id
                     AND current.version=d.current_version
                    LEFT JOIN LATERAL (
                        SELECT * FROM skill_versions value
                        WHERE value.skill_id=d.skill_id
                        ORDER BY value.created_at DESC,value.version DESC LIMIT 1
                    ) latest ON TRUE
                    {where}
                    ORDER BY d.name,d.skill_id"""
            ).fetchall()
        from joyhousebot.storage.postgres_store import _iso

        return [
            {
                "skill_id": str(row["skill_id"]),
                "name": str(row["name"]),
                "description": str(row["description"]),
                "status": str(row["status"]),
                "current_version": row["current_version"],
                "tags": list(row["tags"]),
                "current": (
                    {
                        "version": str(row["current_version"]),
                        "status": str(row["current_status"]),
                        "content_sha256": str(row["current_content_sha256"]),
                        "published_at": _iso(row["current_published_at"]),
                    }
                    if row["current_version"] is not None
                    else None
                ),
                "latest": (
                    {
                        "version": str(row["latest_version"]),
                        "status": str(row["latest_status"]),
                        "content_sha256": str(row["latest_content_sha256"]),
                        "updated_at": _iso(row["latest_updated_at"]),
                    }
                    if row["latest_version"] is not None
                    else None
                ),
                "created_at": _iso(row["created_at"]),
                "updated_at": _iso(row["updated_at"]),
            }
            for row in rows
        ]

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        value = next(
            (item for item in self.list_skills() if item["skill_id"] == skill_id), None
        )
        if value is not None:
            value["versions"] = self.list_skill_versions(skill_id)
        return value

    def list_skill_versions(self, skill_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT v.*,d.name,d.description,d.tags,d.status AS definition_status
                   FROM skill_versions v JOIN skill_definitions d USING(skill_id)
                   WHERE v.skill_id=%s ORDER BY v.created_at DESC,v.version DESC""",
                (skill_id,),
            ).fetchall()
        return [self._skill_version_dict(row) for row in rows]

    def get_skill_version(self, skill_id: str, version: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT v.*,d.name,d.description,d.tags,d.status AS definition_status
                   FROM skill_versions v JOIN skill_definitions d USING(skill_id)
                   WHERE v.skill_id=%s AND v.version=%s""",
                (skill_id, version),
            ).fetchone()
        return self._skill_version_dict(row) if row else None

    def get_published_skill(
        self, skill_id: str, version: str | None = None
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            if version is None:
                row = conn.execute(
                    """SELECT v.*,d.name,d.description,d.tags,
                              d.status AS definition_status
                       FROM skill_definitions d JOIN skill_versions v
                         ON v.skill_id=d.skill_id AND v.version=d.current_version
                       WHERE d.skill_id=%s AND d.status='active'
                         AND v.status='published'""",
                    (skill_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT v.*,d.name,d.description,d.tags,
                              d.status AS definition_status
                       FROM skill_versions v JOIN skill_definitions d USING(skill_id)
                       WHERE v.skill_id=%s AND v.version=%s AND d.status='active'
                         AND v.status IN ('published','retired')""",
                    (skill_id, version),
                ).fetchone()
        return self._skill_version_dict(row) if row else None

    def set_skill_status(self, skill_id: str, *, status: str, actor_id: str) -> bool:
        if status not in {"active", "disabled", "archived"}:
            raise ValueError("invalid Skill status")
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE skill_definitions SET status=%s,updated_at=clock_timestamp()
                   WHERE skill_id=%s AND status<>%s RETURNING current_version""",
                (status, skill_id, status),
            ).fetchone()
            if row is None:
                exists = conn.execute(
                    "SELECT 1 FROM skill_definitions WHERE skill_id=%s", (skill_id,)
                ).fetchone()
                return bool(exists)
            self._append_configuration_event(
                conn,
                "skill",
                skill_id,
                str(row["current_version"] or "definition"),
                f"definition.{status}",
                actor_id,
            )
            self._notify(conn, f"config:skill:{skill_id}")
        return True

    @staticmethod
    def _skill_version_dict(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "skill_id": str(row["skill_id"]),
            "version": str(row["version"]),
            "name": str(row["name"]),
            "description": str(row["description"]),
            "definition_status": str(row["definition_status"]),
            "status": str(row["status"]),
            "instruction_content": str(row["instruction_content"]),
            "tags": list(row["tags"]),
            "input_schema": dict(row["input_schema"]),
            "output_schema": dict(row["output_schema"]),
            "required_capabilities": list(row["required_capabilities"]),
            "required_integrations": list(row["required_integrations"]),
            "examples": list(row["examples"]),
            "eval_cases": list(row["eval_cases"]),
            "templates": list(row["templates"]),
            "change_note": str(row["change_note"]),
            "source": dict(row["source"]),
            "content_sha256": str(row["content_sha256"]),
            "validation_report": dict(row["validation_report"]),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "validated_at": _iso(row["validated_at"]),
            "published_at": _iso(row["published_at"]),
        }
