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
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "operation": self.operation,
            "schema_version": self.schema_version,
            "data": self.data,
            "metadata": self.metadata,
        }
