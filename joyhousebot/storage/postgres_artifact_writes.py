"""Transactional immutable Runtime Artifact writes."""

from __future__ import annotations

import json
from typing import Any

from joyhousebot.domain.identity import payload_hash
from joyhousebot.storage.json_codec import Jsonb


def _json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def insert_runtime_artifact_in_transaction(
    connection: Any,
    *,
    artifact_id: str,
    run_id: str,
    name: str,
    media_type: str,
    content: Any = None,
    uri: str | None = None,
    task_id: str | None = None,
    artifact_type: str = "runtime.output",
    operation: str = "create",
    schema_version: int = 1,
    metadata: dict[str, Any] | None = None,
    content_sha256: str = "",
    object_version: str = "",
    provenance: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> None:
    """Insert one Artifact using the caller's transaction and reject mutation."""
    if not artifact_id.strip() or not artifact_type.strip():
        raise ValueError("artifact id and type are required")
    if not operation.strip() or len(operation) > 64:
        raise ValueError("artifact operation is invalid")
    if int(schema_version) < 1:
        raise ValueError("artifact schema_version must be positive")
    computed_hash = payload_hash(content) if content is not None else ""
    if content_sha256 and computed_hash and content_sha256 != computed_hash:
        raise ValueError("artifact content_sha256 does not match embedded content")
    frozen = {
        "run_id": run_id,
        "task_id": task_id,
        "name": name,
        "media_type": media_type,
        "content": content,
        "uri": uri,
        "artifact_type": artifact_type,
        "operation": operation,
        "schema_version": int(schema_version),
        "metadata": dict(metadata or {}),
        "content_sha256": content_sha256 or computed_hash,
        "object_version": str(object_version or ""),
        "provenance": {
            **dict(provenance or {}),
            "run_id": run_id,
            "task_id": task_id,
        },
        "evidence": dict(evidence or {}),
    }
    owner = connection.execute(
        "SELECT 1 FROM runtime_runs WHERE run_id=%s FOR SHARE", (run_id,)
    ).fetchone()
    if owner is None:
        raise ValueError("artifact Run does not exist")
    if task_id is not None:
        task = connection.execute(
            "SELECT 1 FROM runtime_tasks WHERE task_id=%s AND run_id=%s FOR SHARE",
            (task_id, run_id),
        ).fetchone()
        if task is None:
            raise ValueError("artifact Task does not belong to its Run")
    inserted = connection.execute(
        """INSERT INTO runtime_artifacts
               (artifact_id,run_id,task_id,name,media_type,content,uri,
                artifact_type,operation,schema_version,metadata,content_sha256,
                object_version,provenance,evidence)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(artifact_id) DO NOTHING""",
        (
            artifact_id,
            frozen["run_id"],
            frozen["task_id"],
            frozen["name"],
            frozen["media_type"],
            Jsonb(content) if content is not None else None,
            frozen["uri"],
            frozen["artifact_type"],
            frozen["operation"],
            frozen["schema_version"],
            Jsonb(frozen["metadata"]),
            frozen["content_sha256"],
            frozen["object_version"],
            Jsonb(frozen["provenance"]),
            Jsonb(frozen["evidence"]),
        ),
    )
    if inserted.rowcount:
        return
    row = connection.execute(
        "SELECT * FROM runtime_artifacts WHERE artifact_id=%s FOR SHARE", (artifact_id,)
    ).fetchone()
    assert row is not None
    stored = {
        "run_id": str(row["run_id"]),
        "task_id": row["task_id"],
        "name": str(row["name"]),
        "media_type": str(row["media_type"]),
        "content": _json(row["content"]),
        "uri": row["uri"],
        "artifact_type": str(row["artifact_type"]),
        "operation": str(row["operation"]),
        "schema_version": int(row["schema_version"]),
        "metadata": dict(_json(row["metadata"], {})),
        "content_sha256": str(row["content_sha256"]),
        "object_version": str(row["object_version"]),
        "provenance": dict(_json(row["provenance"], {})),
        "evidence": dict(_json(row["evidence"], {})),
    }
    if stored != frozen:
        raise ValueError("runtime Artifact is immutable; use a new artifact_id for new content")
