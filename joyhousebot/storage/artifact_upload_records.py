"""Persistence records for scoped Host Artifact upload grants."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class ArtifactUploadGrantRecord:
    grant_id: str
    user_id: str
    run_id: str
    task_id: str | None
    action_id: str
    reconciliation_id: str
    operation_id: str
    artifact_id: str
    name: str
    media_type: str
    expected_sha256: str
    expected_size: int
    status: str
    storage_uri: str | None
    object_version: str | None
    provenance: dict[str, Any]
    expires_at: str
    lease_owner: str | None
    lease_expires_at: str | None
    lease_version: int
    created_at: str
    uploaded_at: str | None
    materialized_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
