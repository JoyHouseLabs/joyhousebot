"""Versioned works and revocable sharing built from durable Run artifacts."""

from __future__ import annotations

from typing import Any

from porthouse.storage.json_codec import Jsonb
from porthouse.storage.postgres_work_handoffs import PostgresWorkHandoffStoreMixin
from porthouse.storage.postgres_work_records import PostgresWorkRecordStoreMixin
from porthouse.storage.postgres_work_rows import share_row


class PostgresWorkStoreMixin(PostgresWorkHandoffStoreMixin, PostgresWorkRecordStoreMixin):
    def migrate_works(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS works (
            work_id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            public_slug TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            visibility TEXT NOT NULL DEFAULT 'private',
            data_classification TEXT NOT NULL DEFAULT 'internal',
            current_version INTEGER NOT NULL DEFAULT 0,
            published_version INTEGER,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            published_at TIMESTAMPTZ,
            archived_at TIMESTAMPTZ,
            CHECK (status IN ('draft','published','archived')),
            CHECK (visibility IN ('private','unlisted','public')),
            CHECK (data_classification IN
                ('public','internal','confidential','restricted'))
        );
        CREATE INDEX IF NOT EXISTS ix_works_owner_updated
            ON works(owner_user_id,updated_at DESC);
        CREATE INDEX IF NOT EXISTS ix_works_public
            ON works(public_slug) WHERE status='published' AND visibility='public';
        CREATE TABLE IF NOT EXISTS work_versions (
            work_id TEXT NOT NULL REFERENCES works(work_id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            source_run_id TEXT NOT NULL REFERENCES runtime_runs(run_id),
            source_artifact_id TEXT NOT NULL REFERENCES runtime_artifacts(artifact_id),
            media_type TEXT NOT NULL,
            content JSONB,
            uri TEXT,
            content_sha256 TEXT NOT NULL,
            change_note TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (work_id,version)
        );
        CREATE TABLE IF NOT EXISTS work_collaborators (
            work_id TEXT NOT NULL REFERENCES works(work_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            granted_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (work_id,user_id),
            CHECK (role IN ('viewer','editor'))
        );
        CREATE TABLE IF NOT EXISTS work_shares (
            share_id TEXT PRIMARY KEY,
            work_id TEXT NOT NULL REFERENCES works(work_id) ON DELETE CASCADE,
            version INTEGER,
            token_hash TEXT NOT NULL UNIQUE,
            permission TEXT NOT NULL DEFAULT 'view',
            status TEXT NOT NULL DEFAULT 'active',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            expires_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            revoked_by TEXT,
            CHECK (permission IN ('view','download')),
            CHECK (status IN ('active','revoked')),
            FOREIGN KEY (work_id,version) REFERENCES work_versions(work_id,version)
        );
        CREATE INDEX IF NOT EXISTS ix_work_shares_work
            ON work_shares(work_id,created_at DESC);
        CREATE TABLE IF NOT EXISTS work_access_audit (
            audit_id TEXT PRIMARY KEY,
            work_id TEXT NOT NULL REFERENCES works(work_id) ON DELETE CASCADE,
            version INTEGER,
            share_id TEXT REFERENCES work_shares(share_id),
            event_type TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_work_access_audit_work
            ON work_access_audit(work_id,created_at DESC);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (872341935,))
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="works",
                version=1,
                ddl=ddl,
                description="versioned works, collaborators, shares, and access audit",
            )
            evidence_ddl = """
            ALTER TABLE work_versions
                ADD COLUMN IF NOT EXISTS source_artifact_sha256 TEXT NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS source_object_version TEXT NOT NULL DEFAULT '',
                ADD COLUMN IF NOT EXISTS evidence_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
                ADD COLUMN IF NOT EXISTS evidence_manifest_sha256 TEXT NOT NULL DEFAULT '';
            UPDATE work_versions SET source_artifact_sha256=content_sha256
                WHERE source_artifact_sha256='';
            """
            conn.execute(evidence_ddl)
            self._record_migration(
                conn,
                name="works",
                version=2,
                ddl=evidence_ddl,
                description="freeze Artifact provenance and execution evidence per Work version",
            )
        self.migrate_work_handoffs()

    def create_work_from_artifact(self, *, value: dict[str, Any]) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            existing = conn.execute(
                "SELECT * FROM works WHERE work_id=%s", (value["work_id"],)
            ).fetchone()
            if existing is not None:
                if str(existing["owner_user_id"]) != value["owner_user_id"]:
                    raise ValueError("work identity conflict")
                return self._work(conn, existing, include_content=True)
            artifact = conn.execute(
                """SELECT artifact.* FROM runtime_artifacts artifact
                   JOIN runtime_runs run ON run.run_id=artifact.run_id
                   WHERE artifact.artifact_id=%s AND artifact.run_id=%s
                     AND run.user_id=%s FOR SHARE""",
                (
                    value["source_artifact_id"],
                    value["source_run_id"],
                    value["owner_user_id"],
                ),
            ).fetchone()
            if artifact is None:
                raise ValueError("source artifact not found for owner")
            digest, object_version, evidence, evidence_sha256 = self._artifact_snapshot(
                conn, artifact
            )
            work = conn.execute(
                """INSERT INTO works
                       (work_id,owner_user_id,public_slug,title,description,
                        data_classification,metadata,current_version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,1) RETURNING *""",
                (
                    value["work_id"],
                    value["owner_user_id"],
                    value["public_slug"],
                    value["title"],
                    value.get("description", ""),
                    value.get("data_classification", "internal"),
                    Jsonb(value.get("metadata") or {}),
                ),
            ).fetchone()
            conn.execute(
                """INSERT INTO work_versions
                       (work_id,version,source_run_id,source_artifact_id,media_type,
                        content,uri,content_sha256,source_artifact_sha256,
                        source_object_version,evidence_manifest,evidence_manifest_sha256,
                        change_note,created_by)
                   VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    value["work_id"],
                    artifact["run_id"],
                    artifact["artifact_id"],
                    artifact["media_type"],
                    Jsonb(artifact["content"]) if artifact["content"] is not None else None,
                    artifact["uri"],
                    digest,
                    digest,
                    object_version,
                    Jsonb(evidence),
                    evidence_sha256,
                    value.get("change_note", "Initial version"),
                    value["created_by"],
                ),
            )
            self._work_audit(
                conn,
                audit_id=value["audit_id"],
                work_id=value["work_id"],
                version=1,
                event_type="work.created",
                actor_id=value["created_by"],
                data={"source_artifact_id": artifact["artifact_id"]},
            )
            assert work is not None
            return self._work(conn, work, include_content=True)

    def add_work_version(self, work_id: str, *, value: dict[str, Any]) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            work = conn.execute(
                """SELECT work.* FROM works work WHERE work.work_id=%s AND
                       (work.owner_user_id=%s OR EXISTS (
                           SELECT 1 FROM work_collaborators collaborator
                           WHERE collaborator.work_id=work.work_id
                             AND collaborator.user_id=%s AND collaborator.role='editor'))
                   FOR UPDATE""",
                (work_id, value["actor_user_id"], value["actor_user_id"]),
            ).fetchone()
            if work is None or str(work["status"]) == "archived":
                raise ValueError("editable work not found for owner")
            artifact = conn.execute(
                """SELECT artifact.* FROM runtime_artifacts artifact
                   JOIN runtime_runs run ON run.run_id=artifact.run_id
                   WHERE artifact.artifact_id=%s AND artifact.run_id=%s
                     AND run.user_id=%s FOR SHARE""",
                (
                    value["source_artifact_id"],
                    value["source_run_id"],
                    value["actor_user_id"],
                ),
            ).fetchone()
            if artifact is None:
                raise ValueError("source artifact not found for owner")
            digest, object_version, evidence, evidence_sha256 = self._artifact_snapshot(
                conn, artifact
            )
            version = int(work["current_version"]) + 1
            conn.execute(
                """INSERT INTO work_versions
                       (work_id,version,source_run_id,source_artifact_id,media_type,
                        content,uri,content_sha256,source_artifact_sha256,
                        source_object_version,evidence_manifest,evidence_manifest_sha256,
                        change_note,created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    work_id,
                    version,
                    artifact["run_id"],
                    artifact["artifact_id"],
                    artifact["media_type"],
                    Jsonb(artifact["content"]) if artifact["content"] is not None else None,
                    artifact["uri"],
                    digest,
                    digest,
                    object_version,
                    Jsonb(evidence),
                    evidence_sha256,
                    value.get("change_note", ""),
                    value["created_by"],
                ),
            )
            work = conn.execute(
                """UPDATE works SET current_version=%s,status='draft',
                       updated_at=clock_timestamp()
                   WHERE work_id=%s RETURNING *""",
                (version, work_id),
            ).fetchone()
            self._work_audit(
                conn,
                audit_id=value["audit_id"],
                work_id=work_id,
                version=version,
                event_type="work.version_created",
                actor_id=value["created_by"],
                data={"source_artifact_id": artifact["artifact_id"]},
            )
            assert work is not None
            return self._work(conn, work, include_content=True)

    def update_work(self, work_id: str, *, value: dict[str, Any]) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            work = conn.execute(
                "SELECT * FROM works WHERE work_id=%s AND owner_user_id=%s FOR UPDATE",
                (work_id, value["owner_user_id"]),
            ).fetchone()
            if work is None:
                raise ValueError("work not found for owner")
            if str(work["status"]) == "archived" and value.get("status") != "archived":
                raise ValueError("archived works cannot be reopened")
            status = value.get("status", work["status"])
            visibility = value.get("visibility", work["visibility"])
            classification = value.get(
                "data_classification", work["data_classification"]
            )
            if status == "published" and int(work["current_version"]) < 1:
                raise ValueError("work has no publishable version")
            if status == "published":
                publishable = conn.execute(
                    """SELECT content,uri,content_sha256,source_object_version
                       FROM work_versions WHERE work_id=%s AND version=%s""",
                    (work_id, int(work["current_version"])),
                ).fetchone()
                if publishable is None or not str(publishable["content_sha256"] or ""):
                    raise ValueError("work version lacks an immutable content digest")
                if (
                    publishable["content"] is None
                    and publishable["uri"]
                    and not str(publishable["source_object_version"] or "")
                ):
                    raise ValueError("URI work version lacks a frozen object version")
            if visibility == "public" and classification != "public":
                raise ValueError("public works require public data classification")
            if visibility == "unlisted" and classification in {"confidential", "restricted"}:
                raise ValueError("classified works cannot use unlisted visibility")
            work = conn.execute(
                """UPDATE works SET title=%s,description=%s,status=%s,visibility=%s,
                       data_classification=%s,metadata=%s,
                       published_version=CASE WHEN %s='published'
                           THEN current_version ELSE published_version END,
                       published_at=CASE WHEN %s='published'
                           THEN clock_timestamp() ELSE published_at END,
                       archived_at=CASE WHEN %s='archived' THEN clock_timestamp() ELSE NULL END,
                       updated_at=clock_timestamp()
                   WHERE work_id=%s RETURNING *""",
                (
                    value.get("title", work["title"]),
                    value.get("description", work["description"]),
                    status,
                    visibility,
                    classification,
                    Jsonb(value.get("metadata", work["metadata"])),
                    status,
                    status,
                    status,
                    work_id,
                ),
            ).fetchone()
            self._work_audit(
                conn,
                audit_id=value["audit_id"],
                work_id=work_id,
                version=int(work["current_version"]),
                event_type=f"work.{status}",
                actor_id=value["actor_id"],
                data={"visibility": visibility, "data_classification": classification},
            )
            assert work is not None
            return self._work(conn, work, include_content=True)

    def get_work(
        self, work_id: str, *, expected_user_id: str, include_content: bool = True
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT work.* FROM works work WHERE work.work_id=%s AND
                       (work.owner_user_id=%s OR EXISTS (
                           SELECT 1 FROM work_collaborators collaborator
                           WHERE collaborator.work_id=work.work_id
                             AND collaborator.user_id=%s))""",
                (work_id, expected_user_id, expected_user_id),
            ).fetchone()
            return self._work(conn, row, include_content=include_content) if row else None

    def list_works(self, *, expected_user_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT DISTINCT work.* FROM works work
                   LEFT JOIN work_collaborators collaborator
                     ON collaborator.work_id=work.work_id
                   WHERE work.owner_user_id=%s OR collaborator.user_id=%s
                   ORDER BY work.updated_at DESC""",
                (expected_user_id, expected_user_id),
            ).fetchall()
            return [self._work(conn, row, include_content=False) for row in rows]

    def create_work_share(self, work_id: str, *, value: dict[str, Any]) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            work = conn.execute(
                "SELECT * FROM works WHERE work_id=%s AND owner_user_id=%s FOR UPDATE",
                (work_id, value["owner_user_id"]),
            ).fetchone()
            if work is None or work["published_version"] is None:
                raise ValueError("only an owner can share a published work")
            if str(work["data_classification"]) in {"confidential", "restricted"}:
                raise ValueError("classified works cannot be shared by bearer link")
            version = int(value.get("version") or work["published_version"])
            if version != int(work["published_version"]):
                raise ValueError("only the current published version can be shared")
            exists = conn.execute(
                "SELECT 1 FROM work_versions WHERE work_id=%s AND version=%s",
                (work_id, version),
            ).fetchone()
            if exists is None:
                raise ValueError("shared work version not found")
            row = conn.execute(
                """INSERT INTO work_shares
                       (share_id,work_id,version,token_hash,permission,created_by,expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,
                           CASE WHEN %s::int IS NULL THEN NULL
                                ELSE clock_timestamp()+(%s*interval '1 second') END)
                   RETURNING *""",
                (
                    value["share_id"],
                    work_id,
                    version,
                    value["token_hash"],
                    value.get("permission", "view"),
                    value["created_by"],
                    value.get("expires_in_seconds"),
                    value.get("expires_in_seconds"),
                ),
            ).fetchone()
            self._work_audit(
                conn,
                audit_id=value["audit_id"],
                work_id=work_id,
                version=version,
                share_id=value["share_id"],
                event_type="share.created",
                actor_id=value["created_by"],
                data={"permission": value.get("permission", "view")},
            )
            assert row is not None
            return share_row(row)

    def revoke_work_share(
        self, share_id: str, *, expected_user_id: str, actor_id: str, audit_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE work_shares share SET status='revoked',revoked_by=%s,
                       revoked_at=clock_timestamp()
                   FROM works work WHERE share.share_id=%s AND share.work_id=work.work_id
                     AND work.owner_user_id=%s AND share.status='active'
                   RETURNING share.*""",
                (actor_id, share_id, expected_user_id),
            ).fetchone()
            if row:
                self._work_audit(
                    conn,
                    audit_id=audit_id,
                    work_id=str(row["work_id"]),
                    version=int(row["version"]),
                    share_id=share_id,
                    event_type="share.revoked",
                    actor_id=actor_id,
                    data={},
                )
        return share_row(row) if row else None

    def list_work_shares(
        self, work_id: str, *, expected_user_id: str
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT share.* FROM work_shares share JOIN works work
                     ON work.work_id=share.work_id
                   WHERE share.work_id=%s AND work.owner_user_id=%s
                   ORDER BY share.created_at DESC""",
                (work_id, expected_user_id),
            ).fetchall()
        return [share_row(row) for row in rows]

    def grant_work_collaborator(
        self, work_id: str, *, value: dict[str, Any]
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            owner = conn.execute(
                "SELECT owner_user_id FROM works WHERE work_id=%s AND owner_user_id=%s",
                (work_id, value["owner_user_id"]),
            ).fetchone()
            if owner is None or value["user_id"] == value["owner_user_id"]:
                raise ValueError("work owner cannot be added as a collaborator")
            row = conn.execute(
                """INSERT INTO work_collaborators(work_id,user_id,role,granted_by)
                   VALUES (%s,%s,%s,%s) ON CONFLICT (work_id,user_id) DO UPDATE SET
                       role=EXCLUDED.role,granted_by=EXCLUDED.granted_by
                   RETURNING *""",
                (
                    work_id,
                    value["user_id"],
                    value["role"],
                    value["granted_by"],
                ),
            ).fetchone()
            self._work_audit(
                conn,
                audit_id=value["audit_id"],
                work_id=work_id,
                version=None,
                event_type="collaborator.granted",
                actor_id=value["granted_by"],
                data={"user_id": value["user_id"], "role": value["role"]},
            )
            assert row is not None
            return {
                "work_id": str(row["work_id"]),
                "user_id": str(row["user_id"]),
                "role": str(row["role"]),
                "granted_by": str(row["granted_by"]),
            }

    def revoke_work_collaborator(
        self,
        work_id: str,
        user_id: str,
        *,
        expected_user_id: str,
        actor_id: str,
        audit_id: str,
    ) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """DELETE FROM work_collaborators collaborator USING works work
                   WHERE collaborator.work_id=%s AND collaborator.user_id=%s
                     AND collaborator.work_id=work.work_id AND work.owner_user_id=%s
                   RETURNING collaborator.*""",
                (work_id, user_id, expected_user_id),
            ).fetchone()
            if row is None:
                return False
            self._work_audit(
                conn,
                audit_id=audit_id,
                work_id=work_id,
                version=None,
                event_type="collaborator.revoked",
                actor_id=actor_id,
                data={"user_id": user_id},
            )
            return True

    def list_work_collaborators(
        self, work_id: str, *, expected_user_id: str
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT collaborator.* FROM work_collaborators collaborator
                   JOIN works work ON work.work_id=collaborator.work_id
                   WHERE collaborator.work_id=%s AND work.owner_user_id=%s
                   ORDER BY collaborator.created_at""",
                (work_id, expected_user_id),
            ).fetchall()
        return [
            {
                "work_id": str(row["work_id"]),
                "user_id": str(row["user_id"]),
                "role": str(row["role"]),
                "granted_by": str(row["granted_by"]),
            }
            for row in rows
        ]
    def resolve_public_work(
        self,
        *,
        public_slug: str | None = None,
        token_hash: str | None = None,
        audit_id: str,
        actor_id: str = "anonymous",
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn, conn.transaction():
            share = None
            if token_hash:
                share = conn.execute(
                    """SELECT * FROM work_shares WHERE token_hash=%s AND status='active'
                       AND (expires_at IS NULL OR expires_at>clock_timestamp()) FOR UPDATE""",
                    (token_hash,),
                ).fetchone()
                if share is None:
                    return None
                work = conn.execute(
                    """SELECT * FROM works WHERE work_id=%s AND status<>'archived'
                       AND published_version IS NOT NULL
                       AND data_classification NOT IN ('confidential','restricted')""",
                    (share["work_id"],),
                ).fetchone()
                version = int(share["version"])
            else:
                work = conn.execute(
                    """SELECT * FROM works WHERE public_slug=%s AND status<>'archived'
                       AND published_version IS NOT NULL AND visibility='public'
                       AND data_classification='public'""",
                    (public_slug,),
                ).fetchone()
                version = int(work["published_version"]) if work else 0
            if work is None:
                return None
            value = self._work(conn, work, include_content=True, version=version)
            if share is not None:
                value["share_permission"] = str(share["permission"])
            self._work_audit(
                conn,
                audit_id=audit_id,
                work_id=str(work["work_id"]),
                version=version,
                share_id=str(share["share_id"]) if share else None,
                event_type="share.accessed" if share else "work.public_accessed",
                actor_id=actor_id,
                data={},
            )
            return value

    def list_work_audit(
        self, work_id: str, *, expected_user_id: str
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT audit.* FROM work_access_audit audit JOIN works work
                     ON work.work_id=audit.work_id
                   WHERE audit.work_id=%s AND work.owner_user_id=%s
                   ORDER BY audit.created_at DESC LIMIT 1000""",
                (work_id, expected_user_id),
            ).fetchall()
        from porthouse.storage.postgres_store import _iso

        return [
            {
                "audit_id": str(row["audit_id"]),
                "work_id": str(row["work_id"]),
                "version": int(row["version"]) if row["version"] else None,
                "share_id": str(row["share_id"]) if row["share_id"] else None,
                "event_type": str(row["event_type"]),
                "actor_id": str(row["actor_id"]),
                "data": dict(row["data"] or {}),
                "created_at": _iso(row["created_at"]),
            }
            for row in rows
        ]
