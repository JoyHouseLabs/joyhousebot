"""Typed projections of immutable Graph revisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class GraphNodeRecord:
    revision_id: str
    node_id: str
    node_type: str
    position: int
    definition: dict[str, Any]
    definition_hash: str


@dataclass(slots=True)
class GraphEdgeRecord:
    revision_id: str
    edge_id: str
    source_node_id: str
    target_node_id: str
    edge_type: str
    condition: dict[str, Any]


@dataclass(slots=True)
class GraphRevisionRecord:
    revision_id: str
    run_id: str
    user_id: str
    revision_number: int
    parent_revision_id: str | None
    source: str
    spec_hash: str
    settings: dict[str, Any]
    status: str
    created_by: str
    created_at: str
    nodes: list[GraphNodeRecord]
    edges: list[GraphEdgeRecord]
