"""Business-neutral result artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class Artifact:
    """A structured output produced by a run or task.

    ``artifact_type`` and ``data`` are deliberately opaque to the framework;
    business plugins own their schemas and namespace them in metadata.
    """

    artifact_type: str
    data: Any
    artifact_id: str = field(default_factory=lambda: f"artifact_{uuid4().hex}")
    operation: str = "create"
    schema_version: int = 1
    media_type: str = "application/json"
    uri: str | None = None
    content_sha256: str = ""
    object_version: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.artifact_id.strip() or not self.artifact_type.strip():
            raise ValueError("artifact id and type are required")
        if not self.operation.strip() or len(self.operation) > 64:
            raise ValueError("artifact operation is invalid")
        if self.schema_version < 1:
            raise ValueError("artifact schema_version must be positive")
        if not self.media_type.strip():
            raise ValueError("artifact media_type is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "operation": self.operation,
            "schema_version": self.schema_version,
            "media_type": self.media_type,
            "data": self.data,
            "uri": self.uri,
            "content_sha256": self.content_sha256,
            "object_version": self.object_version,
            "provenance": self.provenance,
            "evidence": self.evidence,
            "metadata": self.metadata,
        }
