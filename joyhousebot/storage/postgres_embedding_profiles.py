"""PostgreSQL control plane for versioned Knowledge embedding profiles."""

from __future__ import annotations

from typing import Any

from joyhousebot.domain.embedding_profiles import (
    embedding_profile_fingerprint,
    normalize_embedding_profile,
)
from joyhousebot.storage.json_codec import Jsonb


class PostgresEmbeddingProfileStoreMixin:
    def migrate_embedding_profiles(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS embedding_profiles (
            profile_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            current_revision_id TEXT,
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS embedding_profile_revisions (
            profile_id TEXT NOT NULL REFERENCES embedding_profiles(profile_id)
                ON DELETE CASCADE,
            revision_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            configuration JSONB NOT NULL,
            fingerprint TEXT NOT NULL,
            make_default BOOLEAN NOT NULL DEFAULT FALSE,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            published_at TIMESTAMPTZ,
            PRIMARY KEY(profile_id,revision_id),
            UNIQUE(profile_id,version),
            CHECK (status IN ('draft','published','retired'))
        );
        CREATE INDEX IF NOT EXISTS ix_embedding_profile_revisions_status
            ON embedding_profile_revisions(profile_id,status,version DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_embedding_profile_one_published
            ON embedding_profile_revisions(profile_id) WHERE status='published';
        ALTER TABLE embedding_profiles
            ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE embedding_profile_revisions
            ADD COLUMN IF NOT EXISTS make_default BOOLEAN NOT NULL DEFAULT FALSE;
        DROP INDEX IF EXISTS uq_embedding_profile_one_default;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_embedding_profile_one_default_profile
            ON embedding_profiles((1)) WHERE is_default;
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="embedding_profiles",
                version=1,
                ddl=ddl,
                description="versioned Knowledge embedding profiles",
            )

    def save_embedding_profile_revision(
        self,
        profile_id: str,
        *,
        name: str,
        description: str,
        configuration: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        profile_id = str(profile_id).strip().lower()
        raw_configuration = dict(configuration)
        make_default = bool(raw_configuration.pop("is_default", False))
        normalized = normalize_embedding_profile(profile_id, raw_configuration)
        normalized_name = str(name).strip()
        if not normalized_name or len(normalized_name) > 160:
            raise ValueError("embedding profile name is required and must be <= 160 characters")
        normalized_description = str(description or "").strip()
        if len(normalized_description) > 2000:
            raise ValueError("embedding profile description must be <= 2000 characters")
        with self._pool.connection() as conn, conn.transaction():
            self._validate_embedding_provider(conn, normalized)
            conn.execute(
                """INSERT INTO embedding_profiles(profile_id,name,description)
                   VALUES (%s,%s,%s)
                   ON CONFLICT(profile_id) DO UPDATE SET
                       name=excluded.name,description=excluded.description,
                       updated_at=clock_timestamp()""",
                (profile_id, normalized_name, normalized_description),
            )
            version = int(
                conn.execute(
                    """SELECT COALESCE(max(version),0)+1 AS version
                       FROM embedding_profile_revisions WHERE profile_id=%s""",
                    (profile_id,),
                ).fetchone()["version"]
            )
            revision_id = f"{profile_id}:v{version}"
            conn.execute(
                """INSERT INTO embedding_profile_revisions
                       (profile_id,revision_id,version,status,configuration,
                        fingerprint,make_default,created_by)
                   VALUES (%s,%s,%s,'draft',%s,%s,%s,%s)""",
                (
                    profile_id,
                    revision_id,
                    version,
                    Jsonb(normalized),
                    embedding_profile_fingerprint(normalized),
                    make_default,
                    actor_id,
                ),
            )
        result = self.get_embedding_profile_revision(profile_id, revision_id)
        assert result is not None
        return result

    def publish_embedding_profile_revision(
        self, profile_id: str, revision_id: str, *, actor_id: str
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.transaction():
            revision = conn.execute(
                """SELECT * FROM embedding_profile_revisions
                   WHERE profile_id=%s AND revision_id=%s FOR UPDATE""",
                (profile_id, revision_id),
            ).fetchone()
            if revision is None:
                raise ValueError("embedding profile revision not found")
            if str(revision["status"]) != "draft":
                raise ValueError("only a draft embedding profile revision can be published")
            configuration = dict(revision["configuration"])
            self._validate_embedding_provider(conn, configuration, require_published=True)
            self._verify_pgvector(conn)
            if bool(revision["make_default"]):
                conn.execute(
                    "UPDATE embedding_profiles SET is_default=FALSE WHERE is_default"
                )
            conn.execute(
                """UPDATE embedding_profile_revisions SET status='retired'
                   WHERE profile_id=%s AND status='published'""",
                (profile_id,),
            )
            changed = conn.execute(
                """UPDATE embedding_profile_revisions SET status='published',
                       published_at=clock_timestamp()
                   WHERE profile_id=%s AND revision_id=%s AND status='draft'""",
                (profile_id, revision_id),
            ).rowcount
            if changed != 1:
                raise ValueError("embedding profile revision cannot be published")
            conn.execute(
                """UPDATE embedding_profiles SET current_revision_id=%s,
                       is_default=CASE WHEN %s THEN TRUE ELSE is_default END,
                       updated_at=clock_timestamp() WHERE profile_id=%s""",
                (revision_id, bool(revision["make_default"]), profile_id),
            )
            conn.execute(
                """INSERT INTO configuration_events
                       (aggregate_type,aggregate_id,revision_id,event_type,actor_id)
                   VALUES ('embedding_profile',%s,%s,'published',%s)""",
                (profile_id, revision_id, actor_id),
            )
        result = self.get_embedding_profile_revision(profile_id, revision_id)
        assert result is not None
        return result

    def list_embedding_profiles(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT profile.*,revision.status,revision.version,
                          revision.configuration,revision.fingerprint,revision.make_default,
                          revision.created_by,revision.created_at AS revision_created_at,
                          revision.published_at
                   FROM embedding_profiles profile
                   LEFT JOIN embedding_profile_revisions revision
                     ON revision.profile_id=profile.profile_id
                    AND revision.revision_id=profile.current_revision_id
                   ORDER BY profile.name,profile.profile_id"""
            ).fetchall()
        return [self._embedding_profile_dict(row) for row in rows]

    def get_embedding_profile(self, profile_id: str) -> dict[str, Any] | None:
        value = next(
            (item for item in self.list_embedding_profiles() if item["profile_id"] == profile_id),
            None,
        )
        if value is not None:
            value["revisions"] = self.list_embedding_profile_revisions(profile_id)
        return value

    def list_embedding_profile_revisions(self, profile_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM embedding_profile_revisions
                   WHERE profile_id=%s ORDER BY version DESC""",
                (profile_id,),
            ).fetchall()
        return [self._embedding_profile_revision_dict(row) for row in rows]

    def get_embedding_profile_revision(
        self, profile_id: str, revision_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM embedding_profile_revisions
                   WHERE profile_id=%s AND revision_id=%s""",
                (profile_id, revision_id),
            ).fetchone()
        return self._embedding_profile_revision_dict(row) if row else None

    def get_published_embedding_profile(
        self, profile_id: str | None = None, *, allow_retired: bool = False
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            if profile_id:
                row = conn.execute(
                    """SELECT revision.* FROM embedding_profiles profile
                       JOIN embedding_profile_revisions revision
                         ON revision.profile_id=profile.profile_id
                       WHERE ((revision.revision_id=%s AND
                               (revision.status='published' OR
                                (%s AND revision.status='retired')))
                           OR (profile.profile_id=%s AND
                               revision.revision_id=profile.current_revision_id AND
                               revision.status='published'))
                       ORDER BY (revision.revision_id=%s) DESC LIMIT 1""",
                    (profile_id, allow_retired, profile_id, profile_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT revision.* FROM embedding_profiles profile
                       JOIN embedding_profile_revisions revision
                         ON revision.revision_id=profile.current_revision_id
                       WHERE profile.is_default AND revision.status='published'
                       ORDER BY revision.published_at DESC LIMIT 1"""
                ).fetchone()
        return self._embedding_profile_revision_dict(row) if row else None

    def knowledge_vector_readiness(self) -> dict[str, Any]:
        with self._pool.connection() as conn:
            pgvector = conn.execute(
                """SELECT extversion FROM pg_extension WHERE extname='vector'"""
            ).fetchone()
            available = conn.execute(
                """SELECT default_version FROM pg_available_extensions
                   WHERE name='vector'"""
            ).fetchone()
            default_profile = conn.execute(
                """SELECT profile.profile_id,revision.revision_id,
                          revision.configuration
                   FROM embedding_profiles profile
                   JOIN embedding_profile_revisions revision
                     ON revision.revision_id=profile.current_revision_id
                   WHERE profile.is_default AND revision.status='published'
                   ORDER BY revision.published_at DESC LIMIT 1"""
            ).fetchone()
        blockers = []
        if pgvector is None:
            blockers.append("pgvector extension is not installed")
        if default_profile is None:
            blockers.append("no published default embedding profile")
        return {
            "ready": not blockers,
            "pgvector": {
                "installed": pgvector is not None,
                "installed_version": str(pgvector["extversion"]) if pgvector else None,
                "available_version": str(available["default_version"]) if available else None,
            },
            "default_profile": (
                {
                    "profile_id": str(default_profile["profile_id"]),
                    "revision_id": str(default_profile["revision_id"]),
                    "model_id": str(default_profile["configuration"]["model_id"]),
                    "dimensions": int(default_profile["configuration"]["dimensions"]),
                }
                if default_profile
                else None
            ),
            "blockers": blockers,
        }

    @staticmethod
    def _validate_embedding_provider(
        conn: Any, configuration: dict[str, Any], *, require_published: bool = False
    ) -> None:
        revision = conn.execute(
            """SELECT status,configuration FROM model_provider_revisions
               WHERE provider_id=%s AND revision_id=%s""",
            (
                configuration["provider_id"],
                configuration["provider_revision_id"],
            ),
        ).fetchone()
        if revision is None:
            raise ValueError("embedding profile references an unknown provider revision")
        if require_published and str(revision["status"]) != "published":
            raise ValueError("embedding profile provider revision is not published")
        model = next(
            (
                item
                for item in revision["configuration"].get("models") or ()
                if str(item.get("model_id") or "") == configuration["model_id"]
            ),
            None,
        )
        if model is None or str(model.get("kind") or "") != "embedding":
            raise ValueError("embedding profile model is not an embedding model")
        declared_dimensions = int(model.get("dimensions") or 0)
        if declared_dimensions and declared_dimensions != configuration["dimensions"]:
            raise ValueError("embedding profile dimensions do not match the model catalog")

    @staticmethod
    def _verify_pgvector(conn: Any) -> None:
        installed = conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname='vector'"
        ).fetchone()
        if installed is None:
            raise ValueError(
                "pgvector is required before publishing an embedding profile; "
                "install it as a database administrator"
            )

    @staticmethod
    def _embedding_profile_revision_dict(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        return {
            "profile_id": str(row["profile_id"]),
            "revision_id": str(row["revision_id"]),
            "version": int(row["version"]),
            "status": str(row["status"]),
            "configuration": dict(row["configuration"]),
            "fingerprint": str(row["fingerprint"]),
            "make_default": bool(row["make_default"]),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "published_at": _iso(row["published_at"]),
        }

    @staticmethod
    def _embedding_profile_dict(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        current = None
        if row["current_revision_id"] is not None:
            current = {
                "profile_id": str(row["profile_id"]),
                "revision_id": str(row["current_revision_id"]),
                "version": int(row["version"]),
                "status": str(row["status"]),
                "configuration": dict(row["configuration"]),
                "fingerprint": str(row["fingerprint"]),
                "make_default": bool(row["make_default"]),
                "created_by": str(row["created_by"]),
                "created_at": _iso(row["revision_created_at"]),
                "published_at": _iso(row["published_at"]),
            }
        return {
            "profile_id": str(row["profile_id"]),
            "name": str(row["name"]),
            "description": str(row["description"] or ""),
            "current_revision_id": row["current_revision_id"],
            "is_default": bool(row["is_default"]),
            "current_revision": current,
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }


__all__ = ["PostgresEmbeddingProfileStoreMixin"]
