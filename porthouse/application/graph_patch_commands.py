"""Commands for controlled mutation of a running immutable Graph."""

from __future__ import annotations

from dataclasses import dataclass

from porthouse.application.run_commands import GraphTaskCommand


@dataclass(frozen=True, slots=True)
class GraphPatchOperationCommand:
    op: str
    node: GraphTaskCommand


@dataclass(frozen=True, slots=True)
class ApplyGraphPatchCommand:
    base_revision_id: str
    reason: str
    operations: tuple[GraphPatchOperationCommand, ...]
    approve_high_risk: bool = False
    defer_activation: bool = False
    proposer_type: str = "user"
    proposer_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResolveGraphPatchProposalCommand:
    resolution: str
    note: str | None = None
