"""Typed records for explainability, model tracing, and replay."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ExecutionSpanRecord:
    span_id: str
    trace_id: str
    run_id: str
    span_kind: str
    name: str
    status: str
    parent_span_id: str | None = None
    task_id: str | None = None
    turn_id: str | None = None
    worker_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    started_at: str | None = None
    first_token_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    ttft_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ModelInvocationRecord:
    invocation_id: str
    run_id: str
    span_id: str
    provider: str
    model: str
    operation: str
    status: str
    task_id: str | None = None
    turn_id: str | None = None
    attempt: int = 1
    provider_request_id: str | None = None
    agent_revision_id: str | None = None
    request_blob_id: str | None = None
    response_blob_id: str | None = None
    request_hash: str | None = None
    response_hash: str | None = None
    finish_reason: str | None = None
    reasoning_availability: str = "unavailable"
    usage: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    cache_status: str = "miss"
    error: dict[str, Any] | None = None
    started_at: str | None = None
    first_token_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    ttft_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReasoningSegmentRecord:
    segment_id: str
    invocation_id: str
    run_id: str
    sequence: int
    source: str
    kind: str
    content: str
    fidelity: str
    content_format: str = "text"
    provider_block_type: str | None = None
    token_count: int | None = None
    content_hash: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TraceBlobRecord:
    blob_id: str
    run_id: str
    kind: str
    content_type: str
    sha256: str
    size_bytes: int
    content: Any = None
    invocation_id: str | None = None
    storage_uri: str | None = None
    created_at: str | None = None
    expires_at: str | None = None

    def to_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        value = asdict(self)
        if not include_content:
            value.pop("content", None)
        return value


@dataclass(slots=True)
class ReplayRunRecord:
    replay_id: str
    source_run_id: str
    mode: str
    created_by: str
    status: str
    source_turn_id: str | None = None
    new_run_id: str | None = None
    overrides: dict[str, Any] = field(default_factory=dict)
    comparison: dict[str, Any] | None = None
    created_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
