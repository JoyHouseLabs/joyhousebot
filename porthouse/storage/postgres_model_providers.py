"""PostgreSQL control plane for model providers and their model catalogs."""

from __future__ import annotations

from typing import Any

from porthouse.domain.model_providers import (
    model_provider_fingerprint,
    model_provider_public,
    normalize_model_provider,
)
from porthouse.storage.json_codec import Jsonb


class PostgresModelProviderStoreMixin:
    def migrate_model_providers(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS model_providers (
            provider_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            current_revision_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS model_provider_revisions (
            provider_id TEXT NOT NULL REFERENCES model_providers(provider_id)
                ON DELETE CASCADE,
            revision_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            configuration JSONB NOT NULL,
            fingerprint TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            published_at TIMESTAMPTZ,
            PRIMARY KEY(provider_id, revision_id),
            UNIQUE(provider_id, version)
        );
        CREATE INDEX IF NOT EXISTS ix_model_provider_revisions_status
            ON model_provider_revisions(provider_id,status,version DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_model_provider_one_published
            ON model_provider_revisions(provider_id) WHERE status='published';
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="model_providers",
                version=1,
                ddl=ddl,
                description="versioned model provider routes and model catalogs",
            )

    def save_model_provider_revision(
        self,
        provider_id: str,
        *,
        name: str,
        description: str,
        configuration: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        provider_id = str(provider_id).strip().lower()
        normalized = normalize_model_provider(provider_id, configuration)
        fingerprint = model_provider_fingerprint(normalized)
        normalized_name = str(name).strip()
        if not normalized_name or len(normalized_name) > 160:
            raise ValueError("model provider name is required and must be <= 160 characters")
        normalized_description = str(description or "").strip()
        if len(normalized_description) > 2000:
            raise ValueError("model provider description must be <= 2000 characters")
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO model_providers(provider_id,name,description)
                   VALUES (%s,%s,%s)
                   ON CONFLICT(provider_id) DO UPDATE SET
                       name=excluded.name,description=excluded.description,
                       updated_at=clock_timestamp()""",
                (provider_id, normalized_name, normalized_description),
            )
            version = int(
                conn.execute(
                    """SELECT COALESCE(max(version),0)+1 AS version
                       FROM model_provider_revisions WHERE provider_id=%s""",
                    (provider_id,),
                ).fetchone()["version"]
            )
            revision_id = f"{provider_id}:v{version}"
            conn.execute(
                """INSERT INTO model_provider_revisions
                       (provider_id,revision_id,version,status,configuration,
                        fingerprint,created_by)
                   VALUES (%s,%s,%s,'draft',%s,%s,%s)""",
                (
                    provider_id,
                    revision_id,
                    version,
                    Jsonb(normalized),
                    fingerprint,
                    actor_id,
                ),
            )
            self._append_configuration_event(
                conn, "model_provider", provider_id, revision_id, "draft.created", actor_id
            )
        result = self.get_model_provider_revision(provider_id, revision_id)
        assert result is not None
        return result

    def stage_model_provider_revision(
        self,
        provider_id: str,
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
                """SELECT * FROM model_provider_revisions
                   WHERE provider_id=%s AND revision_id=%s FOR UPDATE""",
                (provider_id, revision_id),
            ).fetchone()
            if revision is None:
                raise ValueError("model provider revision not found")
            if str(revision["status"]) not in {"draft", "staged"}:
                raise ValueError("only a draft model provider revision can be published")
            extension_id = str(dict(revision["configuration"])["extension_id"])
            extension = conn.execute(
                """SELECT 1 FROM plugin_releases release
                   LEFT JOIN extension_inventory inventory
                     ON inventory.extension_id=release.plugin_id
                   WHERE release.plugin_id=%s AND release.status='active'
                     AND (inventory.extension_id IS NULL OR
                          (inventory.deployment_allowed AND inventory.desired_active))""",
                (extension_id,),
            ).fetchone()
            if extension is None:
                raise ValueError(
                    f"model provider extension must be active before publishing: {extension_id}"
                )
            conn.execute(
                """UPDATE model_provider_revisions SET status='staged'
                   WHERE provider_id=%s AND revision_id=%s""",
                (provider_id, revision_id),
            )
            rollout_id = self._create_configuration_rollout(
                conn,
                aggregate_type="model_provider",
                aggregate_id=provider_id,
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
                "model_provider",
                provider_id,
                revision_id,
                "publish.requested",
                actor_id,
            )
            self._notify(conn, f"config:model_provider:{provider_id}")
        return rollout_id

    def list_model_providers(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT p.*,
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
                   FROM model_providers p
                   LEFT JOIN model_provider_revisions current
                     ON current.provider_id=p.provider_id
                    AND current.revision_id=p.current_revision_id
                   LEFT JOIN LATERAL (
                       SELECT * FROM model_provider_revisions value
                       WHERE value.provider_id=p.provider_id
                       ORDER BY value.version DESC LIMIT 1
                   ) latest ON TRUE
                   ORDER BY p.name,p.provider_id"""
            ).fetchall()
        return [self._model_provider_dict(row) for row in rows]

    def get_model_provider(self, provider_id: str) -> dict[str, Any] | None:
        value = next(
            (
                item
                for item in self.list_model_providers()
                if item["provider_id"] == provider_id
            ),
            None,
        )
        if value is not None:
            value["revisions"] = self.list_model_provider_revisions(provider_id)
        return value

    def list_model_provider_revisions(self, provider_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM model_provider_revisions
                   WHERE provider_id=%s ORDER BY version DESC""",
                (provider_id,),
            ).fetchall()
        return [self._model_provider_revision_dict(row) for row in rows]

    def get_model_provider_revision(
        self, provider_id: str, revision_id: str
    ) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM model_provider_revisions
                   WHERE provider_id=%s AND revision_id=%s""",
                (provider_id, revision_id),
            ).fetchone()
        return self._model_provider_revision_dict(row) if row else None

    def list_active_model_provider_configurations(self) -> dict[str, dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT p.provider_id,r.revision_id,r.configuration
                   FROM model_providers p
                   JOIN model_provider_revisions r
                     ON r.provider_id=p.provider_id
                    AND r.revision_id=p.current_revision_id
                   LEFT JOIN extension_inventory inventory
                     ON inventory.extension_id=r.configuration->>'extension_id'
                   WHERE r.status='published'
                     AND (inventory.extension_id IS NULL OR
                          (inventory.deployment_allowed AND inventory.desired_active))
                   ORDER BY p.provider_id"""
            ).fetchall()
        return {
            str(row["provider_id"]): {
                **dict(row["configuration"]),
                "_revision_id": str(row["revision_id"]),
            }
            for row in rows
            if bool(dict(row["configuration"]).get("enabled", True))
        }

    def list_active_models(self) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for provider_id, configuration in self.list_active_model_provider_configurations().items():
            for model in configuration.get("models") or ():
                if bool(model.get("enabled", True)):
                    values.append({**dict(model), "provider_id": provider_id})
        return sorted(values, key=lambda item: (str(item["kind"]), str(item["name"])))

    @staticmethod
    def _model_provider_revision_dict(row: Any) -> dict[str, Any]:
        from porthouse.storage.postgres_store import _iso

        return {
            "provider_id": str(row["provider_id"]),
            "revision_id": str(row["revision_id"]),
            "version": int(row["version"]),
            "status": str(row["status"]),
            "configuration": model_provider_public(dict(row["configuration"])),
            "fingerprint": str(row["fingerprint"]),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
            "published_at": _iso(row["published_at"]),
        }

    @staticmethod
    def _model_provider_dict(row: Any) -> dict[str, Any]:
        from porthouse.storage.postgres_store import _iso

        def revision(prefix: str) -> dict[str, Any] | None:
            revision_id = row[f"{prefix}_revision_id"]
            if revision_id is None:
                return None
            value = {
                "revision_id": str(revision_id),
                "version": int(row[f"{prefix}_version"]),
                "status": str(row[f"{prefix}_status"]),
                "fingerprint": str(row[f"{prefix}_fingerprint"]),
                "configuration": model_provider_public(
                    dict(row[f"{prefix}_configuration"])
                ),
            }
            if prefix == "latest":
                value["created_at"] = _iso(row["latest_created_at"])
            return value

        return {
            "provider_id": str(row["provider_id"]),
            "name": str(row["name"]),
            "description": str(row["description"] or ""),
            "current_revision_id": row["current_revision_id"],
            "current_revision": revision("current"),
            "latest_revision": revision("latest"),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }


__all__ = ["PostgresModelProviderStoreMixin"]
