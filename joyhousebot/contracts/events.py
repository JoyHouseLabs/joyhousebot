"""Stable event envelope for framework extensions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class DomainEvent:
    """An extension event carried by the framework event broker."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    namespace: str = "core"
    schema_version: int = 1
    event_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "namespace": self.namespace,
            "type": self.type,
            "schema_version": self.schema_version,
            "data": self.data,
            "created_at": self.created_at,
        }
