"""Small immutable models for the public execution surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Run:
    id: str
    status: str
    progress: dict[str, Any]
    pending_action: str | None
    value: dict[str, Any]

    @property
    def terminal(self) -> bool:
        return self.status in {"succeeded", "failed", "cancelled"}

    @classmethod
    def parse(cls, value: dict[str, Any]) -> "Run":
        return cls(
            id=str(value["id"]),
            status=str(value["status"]),
            progress=dict(value.get("progress") or {}),
            pending_action=str(value["pending_action"]) if value.get("pending_action") else None,
            value=dict(value),
        )


@dataclass(frozen=True, slots=True)
class RunEvent:
    sequence: int
    event: str
    run_id: str
    timestamp: str | None
    data: dict[str, Any]

    @classmethod
    def parse(cls, value: dict[str, Any]) -> "RunEvent":
        return cls(
            sequence=int(value["sequence"]),
            event=str(value["event"]),
            run_id=str(value["run_id"]),
            timestamp=str(value["timestamp"]) if value.get("timestamp") else None,
            data=dict(value.get("data") or {}),
        )


@dataclass(frozen=True, slots=True)
class Page:
    items: list[dict[str, Any]]
    next_cursor: str | None = None

    @classmethod
    def parse(cls, value: dict[str, Any]) -> "Page":
        return cls(
            items=[dict(item) for item in value.get("items") or []],
            next_cursor=str(value["next_cursor"]) if value.get("next_cursor") else None,
        )


__all__ = ["Page", "Run", "RunEvent"]
