"""PostgreSQL control plane for versioned remote Capability connections."""

from __future__ import annotations

from typing import Any

from joyhousebot.domain.remote_connections import (
    normalize_remote_connection,
    remote_connection_fingerprint,
    remote_connection_public,
)
from joyhousebot.storage.json_codec import Jsonb


class PostgresRemoteConnectionStoreMixin:
    def migrate_remote_connections(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS remote_connections (
            connection_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            current_revision_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS remote_connection_revisions (
            connection_id TEXT NOT NULL REFERENCES remote_connections(connection_id)
                ON DELETE CASCADE,
            revision_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            configuration JSONB NOT NULL,
            fingerprint TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            published_at TIMESTAMPTZ,
            PRIMARY KEY(connection_id, revision_id),
            UNIQUE(connection_id, version)
        );
        CREATE INDEX IF NOT EXISTS ix_remote_connection_revisions_status
            ON remote_connection_revisions(connection_id,status,version DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_remote_connection_one_published
            ON remote_connection_revisions(connection_id) WHERE status='published';
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="remote_connections",
                version=1,
                ddl=ddl,
                description=(
                    "versioned secret-reference configuration for remote Capability services"
                ),
            )

    def save_remote_connection_revision(
        self,
        connection_id: str,
        *,
        name: str,
        description: str,
        configuration: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        connection_id = str(connection_id).strip()
        normalized = normalize_remote_connection(connection_id, configuration)
        fingerprint = remote_connection_fingerprint(normalized)
        normalized_name = str(name).strip()
        if not normalized_name or len(normalized_name) > 160:
            raise ValueError("remote connection name is required and must be <= 160 characters")
        normalized_description = str(description or "").strip()
        if len(normalized_description) > 2000:
            raise ValueError("remote connection description must be <= 2000 characters")
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO remote_connections(connection_id,name,description)
                   VALUES (%s,%s,%s)
                   ON CONFLICT(connection_id) DO UPDATE SET
                       name=excluded.name,description=excluded.description,
                       updated_at=clock_timestamp()""",
                (connection_id, normalized_name, normalized_description),
            )
            version_row = conn.execute(
                """SELECT COALESCE(max(version),0)+1 AS version
                   FROM remote_connection_revisions WHERE connection_id=%s""",
                (connection_id,),
            ).fetchone()
            version = int(version_row["version"])
            revision_id = f"{connection_id}:v{version}"
            conn.execute(
                """INSERT INTO remote_connection_revisions
                       (connection_id,revision_id,version,status,configuration,
                        fingerprint,created_by)
                   VALUES (%s,%s,%s,'draft',%s,%s,%s)""",
                (
                    connection_id,
                    revision_id,
                    version,
                    Jsonb(normalized),
                    fingerprint,
                    actor_id,
                ),
            )
            self._append_configuration_event(
                conn,
                "remote_connection",
                connection_id,
                revision_id,
                "draft.created",
                actor_id,
            )
        result = self.get_remote_connection_revision(connection_id, revision_id)
        assert result is not None
        return result

    def stage_remote_connection_revision(
        self,
        connection_id: str,
        revision_id: str,
        *,
        actor_id: str,
        activation_mode: str = "automatic",
        timeout_seconds: int = 300,
        auto_rollback: bool = True,
        require_healthy_workers: bool = True,
    ) -> str:
        with self._pool.connection() as conn, conn.transaction():
            revision = conn.execute(
                """SELECT * FROM remote_connection_revisions
                   WHERE connection_id=%s AND revision_id=%s FOR UPDATE""",
                (connection_id, revision_id),
            ).fetchone()
            if revision is None:
                raise ValueError("remote connection revision not found")
            if str(revision["status"]) not in {"draft", "staged"}:
                raise ValueError("only a draft remote connection revision can be published")
            connector = conn.execute(
                """SELECT 1 FROM extension_releases release
                   LEFT JOIN extension_inventory inventory
                     ON inventory.extension_id=release.extension_id
                   WHERE release.extension_id='connector-http-capability'
                     AND release.status='active'
                     AND (inventory.extension_id IS NULL OR
                          (inventory.deployment_allowed AND inventory.desired_active))"""
            ).fetchone()
            if connector is None:
                raise ValueError(
                    "connector-http-capability must be active before publishing a connection"
                )
            conn.execute(
                """UPDATE remote_connection_revisions SET status='staged'
                   WHERE connection_id=%s AND revision_id=%s""",
                (connection_id, revision_id),
            )
            rollout_id = self._create_configuration_rollout(
                conn,
                aggregate_type="remote_connection",
                aggregate_id=connection_id,
                revision_id=revision_id,
                actor_id=actor_id,
                activation_mode=activation_mode,
                timeout_seconds=timeout_seconds,
                auto_rollback=auto_rollback,
                require_healthy_workers=require_healthy_workers,
                target_worker_capability="agent",
            )
            self._append_configuration_event(
                conn,
                "remote_connection",
                connection_id,
                revision_id,
                "publish.requested",
                actor_id,
            )
            self._notify(conn, f"config:remote_connection:{connection_id}")
        return rollout_id

    def list_remote_connections(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT c.*,
                          current.status AS current_status,
                          current.version AS current_version,
                          current.fingerprint AS current_fingerprint,
                          current.configuration AS current_configuration,
                          latest.revision_id AS latest_revision_id,
                          latest.version AS latest_version,
                          latest.status AS latest_status,
                          latest.fingerprint AS latest_fingerprint,
                          latest.configuration AS latest_configuration,
                          latest.created_at AS latest_created_at
                   FROM remote_connections c
                   LEFT JOIN remote_connection_revisions current
                     ON current.connection_id=c.connection_id
                    AND current.revision_id=c.current_revision_id
                   LEFT JOIN LATERAL (
                       SELECT * FROM remote_connection_revisions value
                       WHERE value.connection_id=c.connection_id
                       ORDER BY value.version DESC LIMIT 1
                   ) latest ON TRUE
                   ORDER BY c.name,c.connection_id"""
            ).fetchall()
        return [self._connection_dict(row) for row in rows]

    def get_remote_connection(self, connection_id: str) -> dict[str, Any] | None:
        values = [
            item
            for item in self.list_remote_connections()
            if item["connection_id"] == connection_id
        ]
        if not values:
            return None
        value = values[0]
        value["revisions"] = self.list_remote_connection_revisions(connection_id)
        return value

    def list_remote_connection_revisions(
        self, connection_id: str
    ) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM remote_connection_revisions
                   WHERE connection_id=%s ORDER BY version DESC""",
                (connection_id,),
            ).fetchall()
        return [self._revision_dict(row) for row in rows]

    def get_remote_connection_revision(
        self, connection_id: str, revision_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM remote_connection_revisions
                   WHERE connection_id=%s AND revision_id=%s""",
                (connection_id, revision_id),
            ).fetchone()
        return self._revision_dict(row) if row else None

    def list_active_remote_connection_configurations(self) -> dict[str, dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT c.connection_id,r.revision_id,r.configuration
                   FROM remote_connections c
                   JOIN remote_connection_revisions r
                     ON r.connection_id=c.connection_id
                    AND r.revision_id=c.current_revision_id
                   LEFT JOIN extension_inventory inventory
                     ON inventory.extension_id='connector-http-capability'
                   WHERE r.status='published'
                     AND (inventory.extension_id IS NULL OR
                          (inventory.deployment_allowed AND inventory.desired_active))
                   ORDER BY c.connection_id"""
            ).fetchall()
        return {
            str(row["connection_id"]): {
                **dict(row["configuration"]),
                "_revision_id": str(row["revision_id"]),
            }
            for row in rows
            if bool(dict(row["configuration"]).get("enabled", True))
        }

    def get_remote_capability_release_statuses(
        self, configuration: dict[str, Any]
    ) -> list[dict[str, Any]]:
        capabilities = list(configuration.get("capabilities") or [])
        output: list[dict[str, Any]] = []
        with self._pool.connection() as conn:
            for capability in capabilities:
                capability_id = str(capability.get("capability_id") or "")
                version = str(capability.get("version") or "")
                row = conn.execute(
                    """SELECT status,definition,created_at,published_at
                       FROM capability_versions
                       WHERE capability_id=%s AND version=%s""",
                    (capability_id, version),
                ).fetchone()
                output.append(
                    {
                        **dict(capability),
                        "release_status": str(row["status"]) if row else "not_loaded",
                        "loaded_definition": dict(row["definition"]) if row else None,
                    }
                )
        return output

    @staticmethod
    def _revision_dict(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        configuration = remote_connection_public(dict(row["configuration"]))
        return {
            "connection_id": str(row["connection_id"]),
            "revision_id": str(row["revision_id"]),
            "version": int(row["version"]),
            "status": str(row["status"]),
            "configuration": configuration,
            "fingerprint": str(row["fingerprint"]),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "published_at": _iso(row["published_at"]),
        }

    @staticmethod
    def _connection_dict(row: Any) -> dict[str, Any]:
        from joyhousebot.storage.postgres_store import _iso

        current_configuration = (
            remote_connection_public(dict(row["current_configuration"]))
            if row["current_configuration"] is not None
            else None
        )
        latest_configuration = (
            remote_connection_public(dict(row["latest_configuration"]))
            if row["latest_configuration"] is not None
            else None
        )
        return {
            "connection_id": str(row["connection_id"]),
            "name": str(row["name"]),
            "description": str(row["description"] or ""),
            "current_revision_id": row["current_revision_id"],
            "current_revision": (
                {
                    "revision_id": row["current_revision_id"],
                    "version": int(row["current_version"]),
                    "status": str(row["current_status"]),
                    "fingerprint": str(row["current_fingerprint"]),
                    "configuration": current_configuration,
                }
                if row["current_revision_id"] is not None
                else None
            ),
            "latest_revision": (
                {
                    "revision_id": str(row["latest_revision_id"]),
                    "version": int(row["latest_version"]),
                    "status": str(row["latest_status"]),
                    "fingerprint": str(row["latest_fingerprint"]),
                    "configuration": latest_configuration,
                    "created_at": _iso(row["latest_created_at"]),
                }
                if row["latest_revision_id"] is not None
                else None
            ),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }


__all__ = ["PostgresRemoteConnectionStoreMixin"]
