"""Owner-scoped Runtime input assets and atomic Run bindings."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from joyhousebot.storage.input_asset_records import InputAssetRecord
from joyhousebot.storage.json_codec import Jsonb


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class PostgresInputAssetStoreMixin:
    def migrate_input_assets(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS runtime_input_assets (
            asset_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            original_name TEXT NOT NULL,
            media_type TEXT NOT NULL,
            content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
            byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
            storage_uri TEXT NOT NULL,
            object_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN ('ready', 'deleted')),
            idempotency_key TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            deleted_at TIMESTAMPTZ
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_runtime_input_assets_idempotency
            ON runtime_input_assets(user_id, idempotency_key);
        CREATE INDEX IF NOT EXISTS ix_runtime_input_assets_owner_created
            ON runtime_input_assets(user_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS runtime_run_input_assets (
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            asset_id TEXT NOT NULL REFERENCES runtime_input_assets(asset_id),
            user_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(run_id, asset_id)
        );
        CREATE INDEX IF NOT EXISTS ix_runtime_run_input_assets_owner
            ON runtime_run_input_assets(user_id, run_id);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="runtime_input_assets",
                version=1,
                ddl=ddl,
                description="immutable owner-scoped input assets and Run bindings",
            )
        event_ddl = """
        CREATE TABLE IF NOT EXISTS runtime_input_asset_events (
            event_id BIGSERIAL PRIMARY KEY,
            asset_id TEXT NOT NULL REFERENCES runtime_input_assets(asset_id),
            user_id TEXT NOT NULL,
            run_id TEXT REFERENCES runtime_runs(run_id) ON DELETE SET NULL,
            event_type TEXT NOT NULL CHECK (event_type IN ('created', 'bound', 'read')),
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_runtime_input_asset_events_owner
            ON runtime_input_asset_events(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_runtime_input_asset_events_asset
            ON runtime_input_asset_events(asset_id, created_at DESC);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(event_ddl)
            self._record_migration(
                conn,
                name="runtime_input_asset_events",
                version=1,
                ddl=event_ddl,
                description="auditable Input Asset lifecycle events",
            )
        event_deletion_ddl = """
        ALTER TABLE runtime_input_asset_events
            DROP CONSTRAINT IF EXISTS runtime_input_asset_events_event_type_check;
        ALTER TABLE runtime_input_asset_events
            ADD CONSTRAINT runtime_input_asset_events_event_type_check
            CHECK (event_type IN ('created', 'bound', 'read', 'deleted'));
        """
        with self._pool.connection() as conn, conn.transaction():
            if not self._migration_is_recorded(
                conn,
                name="runtime_input_asset_events",
                version=2,
                ddl=event_deletion_ddl,
                description="owner deletion event for Runtime Input Assets",
            ):
                conn.execute(event_deletion_ddl)
                self._record_migration(
                    conn,
                    name="runtime_input_asset_events",
                    version=2,
                    ddl=event_deletion_ddl,
                    description="owner deletion event for Runtime Input Assets",
                )

    @staticmethod
    def _input_asset(row: dict[str, Any]) -> InputAssetRecord:
        return InputAssetRecord(
            asset_id=str(row["asset_id"]),
            user_id=str(row["user_id"]),
            original_name=str(row["original_name"]),
            media_type=str(row["media_type"]),
            content_sha256=str(row["content_sha256"]),
            byte_size=int(row["byte_size"]),
            storage_uri=str(row["storage_uri"]),
            object_version=str(row["object_version"]),
            status=str(row["status"]),
            idempotency_key=str(row["idempotency_key"]),
            created_at=str(_iso(row["created_at"])),
            deleted_at=_iso(row.get("deleted_at")),
        )

    def create_input_asset(self, **values: Any) -> tuple[InputAssetRecord, bool]:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """INSERT INTO runtime_input_assets
                       (asset_id,user_id,original_name,media_type,content_sha256,byte_size,
                        storage_uri,object_version,idempotency_key)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id,idempotency_key) DO NOTHING
                   RETURNING *,TRUE AS created""",
                (
                    values["asset_id"],
                    values["user_id"],
                    values["original_name"],
                    values["media_type"],
                    values["content_sha256"],
                    values["byte_size"],
                    values["storage_uri"],
                    values["object_version"],
                    values["idempotency_key"],
                ),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """SELECT *,FALSE AS created FROM runtime_input_assets
                       WHERE user_id=%s AND idempotency_key=%s""",
                    (values["user_id"], values["idempotency_key"]),
                ).fetchone()
                assert row is not None
                immutable = (
                    "content_sha256",
                    "byte_size",
                    "media_type",
                    "original_name",
                    "object_version",
                )
                if any(str(row[key]) != str(values[key]) for key in immutable):
                    raise ValueError("Input Asset Idempotency-Key was reused with different input")
            if row["created"]:
                conn.execute(
                    """INSERT INTO runtime_input_asset_events
                           (asset_id,user_id,event_type,data)
                       VALUES (%s,%s,'created',%s)""",
                    (
                        row["asset_id"],
                        row["user_id"],
                        Jsonb(
                            {
                                "content_sha256": row["content_sha256"],
                                "byte_size": int(row["byte_size"]),
                                "object_version": row["object_version"],
                            }
                        ),
                    ),
                )
            return self._input_asset(row), bool(row["created"])

    def get_input_asset(
        self, asset_id: str, *, expected_user_id: str
    ) -> InputAssetRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT * FROM runtime_input_assets
                   WHERE asset_id=%s AND user_id=%s AND status='ready'""",
                (asset_id, expected_user_id),
            ).fetchone()
        return self._input_asset(row) if row else None

    def delete_input_asset(
        self, asset_id: str, *, expected_user_id: str, actor_id: str
    ) -> InputAssetRecord | None:
        """Soft-delete one owner's asset after every bound Run is terminal."""
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT * FROM runtime_input_assets
                   WHERE asset_id=%s AND user_id=%s FOR UPDATE""",
                (asset_id, expected_user_id),
            ).fetchone()
            if row is None:
                return None
            if str(row["status"]) == "deleted":
                return self._input_asset(row)
            active = conn.execute(
                """SELECT run.run_id FROM runtime_run_input_assets binding
                   JOIN runtime_runs run ON run.run_id=binding.run_id
                   WHERE binding.asset_id=%s AND binding.user_id=%s
                     AND run.user_id=%s
                     AND run.status NOT IN ('completed','failed','cancelled','timed_out')
                   LIMIT 1""",
                (asset_id, expected_user_id, expected_user_id),
            ).fetchone()
            if active is not None:
                raise ValueError("Input Asset is still bound to an active Run")
            deleted = conn.execute(
                """UPDATE runtime_input_assets
                   SET status='deleted',deleted_at=clock_timestamp()
                   WHERE asset_id=%s AND user_id=%s AND status='ready'
                   RETURNING *""",
                (asset_id, expected_user_id),
            ).fetchone()
            assert deleted is not None
            conn.execute(
                """INSERT INTO runtime_input_asset_events
                       (asset_id,user_id,event_type,data)
                   VALUES (%s,%s,'deleted',%s)""",
                (
                    asset_id,
                    expected_user_id,
                    Jsonb({"actor_id": actor_id}),
                ),
            )
            return self._input_asset(deleted)

    def get_bound_input_asset(
        self, asset_id: str, *, run_id: str, expected_user_id: str
    ) -> InputAssetRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT asset.* FROM runtime_input_assets AS asset
                   JOIN runtime_run_input_assets AS binding
                     ON binding.asset_id=asset.asset_id
                   JOIN runtime_runs AS run ON run.run_id=binding.run_id
                   WHERE asset.asset_id=%s AND binding.run_id=%s
                     AND binding.user_id=%s AND run.user_id=%s
                     AND asset.user_id=%s AND asset.status='ready'""",
                (asset_id, run_id, expected_user_id, expected_user_id, expected_user_id),
            ).fetchone()
        return self._input_asset(row) if row else None

    def list_run_input_assets(
        self, run_id: str, *, expected_user_id: str
    ) -> list[InputAssetRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT asset.* FROM runtime_input_assets AS asset
                   JOIN runtime_run_input_assets AS binding
                     ON binding.asset_id=asset.asset_id
                   WHERE binding.run_id=%s AND binding.user_id=%s
                     AND asset.user_id=%s AND asset.status='ready'
                   ORDER BY binding.created_at,asset.asset_id""",
                (run_id, expected_user_id, expected_user_id),
            ).fetchall()
        return [self._input_asset(row) for row in rows]

    def _bind_input_assets_in_transaction(
        self, conn: Any, *, run_id: str, user_id: str, asset_ids: list[str] | tuple[str, ...]
    ) -> None:
        normalized = list(dict.fromkeys(str(item).strip() for item in asset_ids if str(item).strip()))
        if not normalized:
            return
        if len(normalized) > 20:
            raise ValueError("a Run may bind at most 20 input assets")
        rows = conn.execute(
            """SELECT asset_id FROM runtime_input_assets
               WHERE user_id=%s AND status='ready' AND asset_id=ANY(%s) FOR SHARE""",
            (user_id, normalized),
        ).fetchall()
        found = {str(row["asset_id"]) for row in rows}
        missing = [item for item in normalized if item not in found]
        if missing:
            raise ValueError("input asset is unavailable or belongs to another user")
        with conn.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO runtime_run_input_assets(run_id,asset_id,user_id)
                   VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
                [(run_id, asset_id, user_id) for asset_id in normalized],
            )
            cursor.executemany(
                """INSERT INTO runtime_input_asset_events
                       (asset_id,user_id,run_id,event_type,data)
                   VALUES (%s,%s,%s,'bound','{}'::jsonb)""",
                [(asset_id, user_id, run_id) for asset_id in normalized],
            )
        self._audit(
            conn,
            run_id=run_id,
            stage="store.input_assets.bound",
            message="Input Assets bound to Run",
            data={"asset_ids": normalized},
        )

    def audit_input_asset_read(self, *, asset_id: str, run_id: str, user_id: str) -> None:
        with self._pool.connection() as conn, conn.transaction():
            bound = conn.execute(
                """SELECT 1 FROM runtime_run_input_assets
                   WHERE asset_id=%s AND run_id=%s AND user_id=%s""",
                (asset_id, run_id, user_id),
            ).fetchone()
            if bound is None:
                raise ValueError("input asset is not bound to the current Run")
            conn.execute(
                """INSERT INTO runtime_input_asset_events
                       (asset_id,user_id,run_id,event_type,data)
                   VALUES (%s,%s,%s,'read','{}'::jsonb)""",
                (asset_id, user_id, run_id),
            )
            self._audit(
                conn,
                run_id=run_id,
                stage="store.input_asset.read",
                message="Input Asset read by capability",
                data={"asset_id": asset_id},
            )

    @staticmethod
    def _require_same_run_input_assets_in_transaction(
        conn: Any, *, run_id: str, user_id: str, asset_ids: list[str] | tuple[str, ...]
    ) -> None:
        requested = set(str(item).strip() for item in asset_ids if str(item).strip())
        rows = conn.execute(
            """SELECT asset_id FROM runtime_run_input_assets
               WHERE run_id=%s AND user_id=%s""",
            (run_id, user_id),
        ).fetchall()
        existing = {str(row["asset_id"]) for row in rows}
        if existing != requested:
            raise ValueError("Run Idempotency-Key was reused with different input assets")


__all__ = ["PostgresInputAssetStoreMixin"]
