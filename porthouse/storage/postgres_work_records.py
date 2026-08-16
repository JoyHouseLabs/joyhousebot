"""Work row projections and immutable Artifact evidence snapshots."""

from __future__ import annotations

from typing import Any

from porthouse.domain.identity import payload_hash
from porthouse.storage.content_blobs import hydrate_json
from porthouse.storage.json_codec import Jsonb
from porthouse.storage.postgres_work_rows import content_hash


class PostgresWorkRecordStoreMixin:
    @staticmethod
    def _work_audit(
        conn: Any,
        *,
        audit_id: str,
        work_id: str,
        event_type: str,
        actor_id: str,
        data: dict[str, Any],
        version: int | None = None,
        share_id: str | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO work_access_audit
                   (audit_id,work_id,version,share_id,event_type,actor_id,data)
               VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (audit_id, work_id, version, share_id, event_type, actor_id, Jsonb(data)),
        )

    def _work(
        self,
        conn: Any,
        row: Any,
        *,
        include_content: bool,
        version: int | None = None,
    ) -> dict[str, Any]:
        from porthouse.storage.postgres_store import _iso

        selected_version = int(version or row["current_version"])
        version_row = conn.execute(
            "SELECT * FROM work_versions WHERE work_id=%s AND version=%s",
            (row["work_id"], selected_version),
        ).fetchone()
        return {
            "work_id": str(row["work_id"]),
            "owner_user_id": str(row["owner_user_id"]),
            "public_slug": str(row["public_slug"]),
            "title": str(row["title"]),
            "description": str(row["description"]),
            "status": str(row["status"]),
            "visibility": str(row["visibility"]),
            "data_classification": str(row["data_classification"]),
            "current_version": int(row["current_version"]),
            "published_version": (
                int(row["published_version"]) if row["published_version"] else None
            ),
            "metadata": dict(row["metadata"] or {}),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "published_at": _iso(row["published_at"]),
            "archived_at": _iso(row["archived_at"]),
            "version": self._version(version_row, include_content=include_content),
        }

    def _version(self, row: Any, *, include_content: bool) -> dict[str, Any] | None:
        if row is None:
            return None
        from porthouse.storage.postgres_store import _iso

        return {
            "work_id": str(row["work_id"]),
            "version": int(row["version"]),
            "source_run_id": str(row["source_run_id"]),
            "source_artifact_id": str(row["source_artifact_id"]),
            "media_type": str(row["media_type"]),
            "content": (
                hydrate_json(
                    self.blob_store,
                    row["content"],
                    row["uri"],
                    sha256=str(row["content_sha256"]),
                )
                if include_content
                else None
            ),
            "uri": row["uri"] if include_content else None,
            "content_sha256": str(row["content_sha256"]),
            "source_artifact_sha256": str(row["source_artifact_sha256"]),
            "source_object_version": str(row["source_object_version"]),
            "evidence_manifest": (
                dict(row["evidence_manifest"] or {}) if include_content else None
            ),
            "evidence_manifest_sha256": str(row["evidence_manifest_sha256"]),
            "change_note": str(row["change_note"]),
            "created_by": str(row["created_by"]),
            "created_at": _iso(row["created_at"]),
        }

    @staticmethod
    def _artifact_snapshot(
        conn: Any, artifact: Any
    ) -> tuple[str, str, dict[str, Any], str]:
        """Freeze the source identity and proof available at Work version creation."""
        embedded = artifact["content"] is not None
        digest = str(artifact["content_sha256"] or "")
        if embedded and not digest:
            digest = content_hash(artifact["content"], None)
        object_version = str(artifact["object_version"] or "")
        if not embedded and artifact["uri"] and (not digest or not object_version):
            raise ValueError(
                "URI artifacts require content_sha256 and object_version before becoming Works"
            )
        if not digest:
            raise ValueError("source artifact lacks immutable content")
        run = conn.execute(
            """SELECT run_id,root_run_id,agent_id,status,created_at,finished_at
               FROM runtime_runs WHERE run_id=%s""",
            (artifact["run_id"],),
        ).fetchone()
        snapshot = conn.execute(
            "SELECT snapshot FROM run_execution_snapshots WHERE run_id=%s",
            (artifact["run_id"],),
        ).fetchone()
        verifications = conn.execute(
            """SELECT verification_id,task_id,turn_id,attempt,verifier_id,
                      verifier_type,verifier_version,required,status,input_hash,evidence
               FROM verification_records WHERE run_id=%s
               ORDER BY attempt,created_at,verifier_id""",
            (artifact["run_id"],),
        ).fetchall()
        actions = conn.execute(
            """SELECT intent.action_id,intent.task_id,intent.capability_ref,
                      intent.input_hash,intent.status,intent.side_effect,
                      intent.idempotency_key,observation.status AS observed_status,
                      observation.reconciliation_status,observation.operation
               FROM action_intents intent
               LEFT JOIN action_observations observation
                 ON observation.action_id=intent.action_id
               WHERE intent.run_id=%s ORDER BY intent.created_at""",
            (artifact["run_id"],),
        ).fetchall()
        manifest = {
            "schema_version": 1,
            "artifact": {
                "artifact_id": str(artifact["artifact_id"]),
                "artifact_type": str(artifact["artifact_type"]),
                "operation": str(artifact["operation"]),
                "schema_version": int(artifact["schema_version"]),
                "content_sha256": digest,
                "object_version": object_version,
                "provenance": dict(artifact["provenance"] or {}),
                "evidence": dict(artifact["evidence"] or {}),
            },
            "run": (
                {
                    "run_id": str(run["run_id"]),
                    "root_run_id": str(run["root_run_id"] or run["run_id"]),
                    "agent_id": str(run["agent_id"]),
                    "status": str(run["status"]),
                    "created_at": run["created_at"].isoformat(),
                    "finished_at": (
                        run["finished_at"].isoformat() if run["finished_at"] else None
                    ),
                }
                if run
                else None
            ),
            "execution_snapshot": dict(snapshot["snapshot"]) if snapshot else None,
            "verifications": [
                {
                    "verification_id": str(row["verification_id"]),
                    "task_id": row["task_id"],
                    "turn_id": row["turn_id"],
                    "attempt": int(row["attempt"]),
                    "verifier_id": str(row["verifier_id"]),
                    "verifier_type": str(row["verifier_type"]),
                    "verifier_version": str(row["verifier_version"]),
                    "required": bool(row["required"]),
                    "status": str(row["status"]),
                    "input_hash": str(row["input_hash"]),
                    "evidence": dict(row["evidence"] or {}),
                }
                for row in verifications
            ],
            "actions": [
                {
                    "action_id": str(row["action_id"]),
                    "task_id": row["task_id"],
                    "capability_ref": dict(row["capability_ref"] or {}),
                    "input_hash": str(row["input_hash"]),
                    "status": str(row["status"]),
                    "side_effect": str(row["side_effect"]),
                    "idempotency_key": str(row["idempotency_key"]),
                    "observed_status": row["observed_status"],
                    "reconciliation_status": row["reconciliation_status"],
                    "operation": dict(row["operation"] or {}),
                }
                for row in actions
            ],
        }
        return digest, object_version, manifest, payload_hash(manifest)
