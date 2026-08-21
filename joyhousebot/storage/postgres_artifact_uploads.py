"""Scoped, one-use Host Artifact grants and Worker materialization."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from joyhousebot.contracts.events import AgentEvent, EventType, EventVisibility
from joyhousebot.storage.artifact_upload_records import ArtifactUploadGrantRecord
from joyhousebot.storage.json_codec import Jsonb
from joyhousebot.storage.postgres_artifact_writes import (
    insert_runtime_artifact_in_transaction,
)
from joyhousebot.storage.postgres_event_writes import append_runtime_event_in_transaction


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class PostgresArtifactUploadStoreMixin:
    def migrate_artifact_uploads(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS artifact_upload_grants (
            grant_id TEXT PRIMARY KEY,
            token_fingerprint TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id) ON DELETE CASCADE,
            task_id TEXT,
            action_id TEXT NOT NULL REFERENCES action_intents(action_id) ON DELETE CASCADE,
            reconciliation_id TEXT NOT NULL
                REFERENCES operation_reconciliations(reconciliation_id) ON DELETE CASCADE,
            operation_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            media_type TEXT NOT NULL,
            expected_sha256 TEXT NOT NULL CHECK (expected_sha256 ~ '^[0-9a-f]{64}$'),
            expected_size BIGINT NOT NULL CHECK (expected_size >= 0),
            status TEXT NOT NULL DEFAULT 'issued'
                CHECK (status IN ('issued','uploaded','materializing','materialized','expired','failed')),
            storage_uri TEXT,
            object_version TEXT,
            provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
            error JSONB,
            expires_at TIMESTAMPTZ NOT NULL,
            lease_owner TEXT,
            lease_expires_at TIMESTAMPTZ,
            lease_version BIGINT NOT NULL DEFAULT 0,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            uploaded_at TIMESTAMPTZ,
            materialized_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_artifact_upload_grants_materialize
            ON artifact_upload_grants(uploaded_at,created_at) WHERE status='uploaded';
        CREATE INDEX IF NOT EXISTS ix_artifact_upload_grants_expiry
            ON artifact_upload_grants(expires_at) WHERE status='issued';
        CREATE INDEX IF NOT EXISTS ix_artifact_upload_grants_owner
            ON artifact_upload_grants(user_id,run_id,created_at DESC);
        """
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(ddl)
            self._record_migration(
                conn,
                name="artifact_upload_grants",
                version=1,
                ddl=ddl,
                description="one-use scoped Host Artifact upload grants",
            )

    def create_artifact_upload_grant(self, **values: Any) -> ArtifactUploadGrantRecord:
        with self._pool.connection() as conn, conn.transaction():
            scope = conn.execute(
                """SELECT rec.operation,rec.action_id,rec.run_id,rec.user_id,intent.task_id
                   FROM operation_reconciliations rec
                   JOIN action_intents intent ON intent.action_id=rec.action_id
                   WHERE rec.reconciliation_id=%s AND rec.run_id=%s AND rec.user_id=%s
                     AND rec.action_id=%s FOR SHARE OF rec,intent""",
                (
                    values["reconciliation_id"],
                    values["run_id"],
                    values["user_id"],
                    values["action_id"],
                ),
            ).fetchone()
            if scope is None:
                raise ValueError("Artifact grant operation scope is invalid")
            operation = dict(scope["operation"] or {})
            frozen_operation_id = str(
                operation.get("remote_operation_id")
                or operation.get("provider_operation_id")
                or operation.get("operation_id")
                or ""
            )
            if frozen_operation_id != values["operation_id"]:
                raise ValueError("Artifact grant operation identity is invalid")
            if values.get("task_id") != scope["task_id"]:
                raise ValueError("Artifact grant Task identity is invalid")
            row = conn.execute(
                """INSERT INTO artifact_upload_grants
                       (grant_id,token_fingerprint,user_id,run_id,task_id,action_id,
                        reconciliation_id,operation_id,artifact_id,name,media_type,
                        expected_sha256,expected_size,provenance,expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           clock_timestamp()+make_interval(secs => %s)) RETURNING *""",
                (
                    values["grant_id"],
                    values["token_fingerprint"],
                    values["user_id"],
                    values["run_id"],
                    values.get("task_id"),
                    values["action_id"],
                    values["reconciliation_id"],
                    values["operation_id"],
                    values["artifact_id"],
                    values["name"],
                    values["media_type"],
                    values["expected_sha256"],
                    values["expected_size"],
                    Jsonb(values.get("provenance") or {}),
                    max(60, min(3600, int(values.get("expires_in_seconds") or 900))),
                ),
            ).fetchone()
        assert row is not None
        return self._artifact_upload_grant(row)

    def get_artifact_upload_grant_by_token(
        self, grant_id: str, *, token_fingerprint: str
    ) -> ArtifactUploadGrantRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """UPDATE artifact_upload_grants SET status='expired',updated_at=clock_timestamp()
                   WHERE grant_id=%s AND status='issued' AND expires_at<=clock_timestamp()""",
                (grant_id,),
            )
            row = conn.execute(
                """SELECT * FROM artifact_upload_grants
                   WHERE grant_id=%s AND token_fingerprint=%s""",
                (grant_id, token_fingerprint),
            ).fetchone()
        return self._artifact_upload_grant(row) if row else None

    def commit_artifact_upload(self, grant_id: str, **values: Any) -> ArtifactUploadGrantRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """UPDATE artifact_upload_grants SET status='uploaded',storage_uri=%s,
                       object_version=%s,token_fingerprint='consumed:'||grant_id,
                       uploaded_at=clock_timestamp(),updated_at=clock_timestamp()
                   WHERE grant_id=%s AND token_fingerprint=%s AND status='issued'
                     AND expires_at>clock_timestamp() AND operation_id=%s AND action_id=%s
                     AND media_type=%s AND expected_sha256=%s AND expected_size=%s
                   RETURNING *""",
                (
                    values["storage_uri"],
                    values["object_version"],
                    grant_id,
                    values["token_fingerprint"],
                    values["operation_id"],
                    values["action_id"],
                    values["media_type"],
                    values["content_sha256"],
                    values["byte_size"],
                ),
            ).fetchone()
            if row is not None:
                self._notify(conn, str(row["run_id"]))
        return self._artifact_upload_grant(row) if row else None

    def claim_artifact_upload(self, *, worker_id: str, lease_seconds: int = 60) -> ArtifactUploadGrantRecord | None:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """WITH candidate AS (
                       SELECT grant_id FROM artifact_upload_grants
                       WHERE (status='uploaded' OR
                              (status='materializing' AND lease_expires_at<clock_timestamp()))
                         AND attempt_count<max_attempts
                       ORDER BY uploaded_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE artifact_upload_grants AS target SET status='materializing',lease_owner=%s,
                       lease_expires_at=clock_timestamp()+make_interval(secs => %s),
                       lease_version=lease_version+1,attempt_count=attempt_count+1,
                       updated_at=clock_timestamp()
                   FROM candidate WHERE target.grant_id=candidate.grant_id RETURNING target.*""",
                (worker_id, max(10, int(lease_seconds))),
            ).fetchone()
        return self._artifact_upload_grant(row) if row else None

    def materialize_artifact_upload(self, grant_id: str, **values: Any) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """SELECT * FROM artifact_upload_grants WHERE grant_id=%s
                   AND status='materializing' AND lease_owner=%s AND lease_version=%s
                   FOR UPDATE""",
                (grant_id, values["worker_id"], values["lease_version"]),
            ).fetchone()
            if row is None:
                return False
            insert_runtime_artifact_in_transaction(
                conn,
                artifact_id=str(row["artifact_id"]),
                run_id=str(row["run_id"]),
                task_id=row["task_id"],
                name=str(row["name"]),
                media_type=str(row["media_type"]),
                uri=str(row["storage_uri"]),
                content_sha256=str(row["expected_sha256"]),
                object_version=str(row["object_version"]),
                artifact_type="host.output",
                provenance={
                    **dict(row["provenance"] or {}),
                    "action_id": str(row["action_id"]),
                    "operation_id": str(row["operation_id"]),
                    "reconciliation_id": str(row["reconciliation_id"]),
                },
                evidence={"upload_grant_id": grant_id},
                blob_store=self.blob_store,
                blob_inline_threshold_bytes=self.blob_inline_threshold_bytes,
            )
            append_runtime_event_in_transaction(
                conn,
                AgentEvent(
                    event_id=f"artifact_upload_materialized_{grant_id}",
                    run_id=str(row["run_id"]),
                    task_id=row["task_id"],
                    type=EventType.ARTIFACT_CREATED.value,
                    phase="execution",
                    status="completed",
                    visibility=EventVisibility.PRIVATE.value,
                    summary=f"Host Artifact 已验证：{str(row['name'])[:300]}",
                    worker_id=values["worker_id"],
                    lease_version=values["lease_version"],
                    data={
                        "artifact_id": str(row["artifact_id"]),
                        "action_id": str(row["action_id"]),
                        "operation_id": str(row["operation_id"]),
                        "content_sha256": str(row["expected_sha256"]),
                    },
                ),
            )
            updated = conn.execute(
                """UPDATE artifact_upload_grants SET status='materialized',
                       lease_owner=NULL,lease_expires_at=NULL,materialized_at=clock_timestamp(),
                       updated_at=clock_timestamp()
                   WHERE grant_id=%s AND lease_owner=%s AND lease_version=%s""",
                (grant_id, values["worker_id"], values["lease_version"]),
            )
            self._notify(conn, str(row["run_id"]))
        return bool(updated.rowcount)

    def fail_artifact_upload(self, grant_id: str, **values: Any) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            updated = conn.execute(
                """UPDATE artifact_upload_grants SET
                       status=CASE WHEN attempt_count>=max_attempts THEN 'failed'
                                   ELSE 'uploaded' END,
                       error=%s,lease_owner=NULL,lease_expires_at=NULL,
                       updated_at=clock_timestamp()
                   WHERE grant_id=%s AND status='materializing' AND lease_owner=%s
                     AND lease_version=%s""",
                (
                    Jsonb(values.get("error") or {}),
                    grant_id,
                    values["worker_id"],
                    values["lease_version"],
                ),
            )
        return bool(updated.rowcount)

    @staticmethod
    def _artifact_upload_grant(row: dict[str, Any]) -> ArtifactUploadGrantRecord:
        return ArtifactUploadGrantRecord(
            grant_id=str(row["grant_id"]), user_id=str(row["user_id"]),
            run_id=str(row["run_id"]), task_id=row["task_id"], action_id=str(row["action_id"]),
            reconciliation_id=str(row["reconciliation_id"]), operation_id=str(row["operation_id"]),
            artifact_id=str(row["artifact_id"]), name=str(row["name"]),
            media_type=str(row["media_type"]), expected_sha256=str(row["expected_sha256"]),
            expected_size=int(row["expected_size"]), status=str(row["status"]),
            storage_uri=row["storage_uri"], object_version=row["object_version"],
            provenance=dict(row["provenance"] or {}), expires_at=_iso(row["expires_at"]) or "",
            lease_owner=row["lease_owner"], lease_expires_at=_iso(row["lease_expires_at"]),
            lease_version=int(row["lease_version"]), created_at=_iso(row["created_at"]) or "",
            uploaded_at=_iso(row["uploaded_at"]), materialized_at=_iso(row["materialized_at"]),
        )
