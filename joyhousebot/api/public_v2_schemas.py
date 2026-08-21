"""Stable schemas for the Owner/Installation public execution surface."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,256}$"


class StrictPublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExchangeInstallationTokenRequest(StrictPublicModel):
    client_id: str = Field(pattern=_ID_PATTERN)
    client_secret: str = Field(min_length=20, max_length=512)
    installation_id: str = Field(pattern=_ID_PATTERN)
    scopes: list[str] = Field(min_length=1, max_length=16)
    ttl_seconds: int = Field(default=900, ge=60, le=3600)


class ExchangeOwnerTokenRequest(StrictPublicModel):
    client_id: str = Field(pattern=_ID_PATTERN)
    subject_token: str = Field(min_length=64, max_length=16_384)
    scopes: list[str] = Field(min_length=1, max_length=8)
    ttl_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_ttl_seconds: int = Field(default=2_592_000, ge=300, le=2_592_000)


class RefreshOwnerTokenRequest(StrictPublicModel):
    client_id: str = Field(pattern=_ID_PATTERN)
    refresh_token: str = Field(min_length=32, max_length=512)
    ttl_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_ttl_seconds: int = Field(default=2_592_000, ge=300, le=2_592_000)


class OwnerTokenResponse(StrictPublicModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"]
    expires_at: str
    refresh_expires_at: str
    scopes: list[str]


class CreateEntryPointRunRequest(StrictPublicModel):
    input: dict[str, Any]
    idempotency_key: str = Field(min_length=1, max_length=256)
    session_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    client_context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def limit_payload_size(self) -> "CreateEntryPointRunRequest":
        size = len(
            json.dumps(
                {"input": self.input, "client_context": self.client_context},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if size > 256 * 1024:
            raise ValueError("EntryPoint input and client_context exceed 256 KiB")
        return self


class EntryPointDescriptor(StrictPublicModel):
    id: str
    key: str
    app_id: str
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    interaction_mode: Literal["auto", "interactive", "background"]
    permission_summary: list[str]
    risk_summary: list[str]


class EntryPointList(StrictPublicModel):
    items: list[EntryPointDescriptor]
    next_cursor: str | None = None


class OwnerAppInstallation(StrictPublicModel):
    installation_id: str
    app_id: str
    version: str
    name: str
    description: str
    status: str
    granted_permissions: list[str]
    manifest_sha256: str
    updated_at: str | None = None


class OwnerAppInstallationList(StrictPublicModel):
    items: list[OwnerAppInstallation]
    next_cursor: str | None = None


class InstallOwnerAppRequest(StrictPublicModel):
    version: str = Field(min_length=1, max_length=64)
    configuration: dict[str, Any] = Field(default_factory=dict)


class RunProgress(StrictPublicModel):
    phase: str | None = None
    summary: str
    completed: int
    total: int


class OperationProgressItem(StrictPublicModel):
    position: int | None = None
    label: str


class PublicOperationProgress(StrictPublicModel):
    id: str
    status: Literal["running", "succeeded", "failed", "needs_attention"]
    summary: str
    percent: float | None = Field(default=None, ge=0, le=100)
    completed: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)
    current_item: OperationProgressItem | None = None
    next_item: OperationProgressItem | None = None
    updated_at: str | None = None


class PublicOperationProgressList(StrictPublicModel):
    items: list[PublicOperationProgress]


class PublicRun(StrictPublicModel):
    id: str
    status: Literal[
        "queued",
        "running",
        "waiting_for_input",
        "waiting_for_approval",
        "succeeded",
        "failed",
        "cancelled",
    ]
    progress: RunProgress
    pending_action: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class ArtifactDescriptor(StrictPublicModel):
    id: str
    run_id: str
    name: str
    type: str
    media_type: str
    schema_version: int
    content: Any = None
    content_sha256: str
    created_at: str | None = None


class ArtifactList(StrictPublicModel):
    items: list[ArtifactDescriptor]
    next_cursor: str | None = None


class ApprovalDescriptor(StrictPublicModel):
    id: str
    run_id: str
    status: str
    summary: str
    risk: str
    data_classification: str
    input_preview: dict[str, Any]
    allowed_decisions: list[Literal["approve", "reject", "request_changes", "revoke"]]
    requested_at: str | None = None
    expires_at: str | None = None
    resolved_at: str | None = None


class ApprovalList(StrictPublicModel):
    items: list[ApprovalDescriptor]
    next_cursor: str | None = None


class DecideApprovalRequest(StrictPublicModel):
    decision: Literal["approve", "reject", "request_changes", "revoke"]
    note: str | None = Field(default=None, max_length=2000)


class ApprovalDecisionResult(StrictPublicModel):
    approval: ApprovalDescriptor
    run: PublicRun


class InputRequestDescriptor(StrictPublicModel):
    id: str
    run_id: str
    question: str
    fields: list[dict[str, Any]]
    presentation: dict[str, Any]
    expires_at: str | None = None
    created_at: str | None = None


class InputRequestList(StrictPublicModel):
    items: list[InputRequestDescriptor]
    next_cursor: str | None = None


class ResolveInputRequest(StrictPublicModel):
    input_request_id: str = Field(pattern=_ID_PATTERN)
    answers: dict[str, Any] = Field(min_length=1)

    @model_validator(mode="after")
    def limit_answers_size(self) -> "ResolveInputRequest":
        size = len(
            json.dumps(self.answers, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        if size > 256 * 1024:
            raise ValueError("input answers exceed 256 KiB")
        return self


class ResolveInputResult(StrictPublicModel):
    run: PublicRun
    pending_inputs: list[InputRequestDescriptor]


__all__ = [
    "ApprovalDecisionResult",
    "ApprovalDescriptor",
    "ApprovalList",
    "ArtifactDescriptor",
    "ArtifactList",
    "CreateEntryPointRunRequest",
    "DecideApprovalRequest",
    "EntryPointDescriptor",
    "EntryPointList",
    "ExchangeInstallationTokenRequest",
    "ExchangeOwnerTokenRequest",
    "InputRequestDescriptor",
    "InputRequestList",
    "OwnerTokenResponse",
    "PublicRun",
    "RefreshOwnerTokenRequest",
    "ResolveInputRequest",
    "ResolveInputResult",
]
