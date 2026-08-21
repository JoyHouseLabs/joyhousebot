"""Versioned request contracts for Device Host registration and delivery."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class DeviceCapabilityDeclaration(BaseModel):
    capability_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    implementation_digest: str = Field(min_length=71, max_length=71)
    portable: bool = False


class RegisterDeviceHostRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)
    host_revision: str = Field(min_length=1, max_length=256)
    host_manifest_digest: str = Field(min_length=71, max_length=71)
    public_key_fingerprint: str | None = Field(default=None, max_length=256)
    is_default: bool = False
    capabilities: list[DeviceCapabilityDeclaration] = Field(min_length=1, max_length=500)


class DeviceHeartbeatRequest(BaseModel):
    host_revision: str = Field(min_length=1, max_length=256)
    host_manifest_digest: str = Field(min_length=71, max_length=71)


class DeviceModelAccessPolicy(BaseModel):
    provider_id: str = Field(min_length=1, max_length=128)
    provider_revision_id: str = Field(min_length=1, max_length=256)
    model_id: str = Field(min_length=1, max_length=256)
    token_budget: int = Field(ge=1, le=100_000_000)
    cost_budget_micros: int = Field(default=0, ge=0, le=100_000_000_000)
    max_concurrent: int = Field(default=1, ge=1, le=32)
    expires_in_seconds: int = Field(default=3600, ge=30, le=86_400)


class DeviceToolAccessDeclaration(BaseModel):
    capability_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)


class CreateDeviceDeliveryRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    operation_id: str = Field(min_length=1, max_length=256)
    portable: bool = False
    deadline_seconds: int = Field(default=86_400, ge=60, le=604_800)
    max_attempts: int = Field(default=20, ge=1, le=100)
    model_access: DeviceModelAccessPolicy | None = None
    tool_access: list[DeviceToolAccessDeclaration] = Field(default_factory=list, max_length=64)


class ClaimDeviceOperationsRequest(BaseModel):
    claim_session_id: str = Field(min_length=16, max_length=256)
    limit: int = Field(default=5, ge=1, le=20)
    lease_seconds: int = Field(default=60, ge=10, le=300)


class DeviceOperationEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    event_type: str = Field(min_length=1, max_length=128)
    summary: str = Field(default="", max_length=500)
    payload: dict[str, Any] = Field(default_factory=dict)


class AppendDeviceOperationEventsRequest(BaseModel):
    claim_session_id: str = Field(min_length=16, max_length=256)
    claim_version: int = Field(ge=1)
    events: list[DeviceOperationEvent] = Field(min_length=1, max_length=100)


class DeviceOperationLeaseHeartbeatRequest(BaseModel):
    claim_session_id: str = Field(min_length=16, max_length=256)
    claim_version: int = Field(ge=1)
    lease_seconds: int = Field(default=60, ge=10, le=300)


class CompleteDeviceOperationRequest(BaseModel):
    claim_session_id: str = Field(min_length=16, max_length=256)
    claim_version: int = Field(ge=1)
    result: dict[str, Any]


class IssueDeviceModelGrantRequest(BaseModel):
    claim_session_id: str = Field(min_length=16, max_length=256)
    claim_version: int = Field(ge=1)


class CreateDeviceHostControlRequest(BaseModel):
    action: Literal[
        "preflight", "diagnose_opencli", "diagnose_pi", "enable_opencli",
        "disable_opencli", "enable_pi", "disable_pi", "restart_host",
    ]
    parameters: dict[str, str] = Field(default_factory=dict, max_length=2)


class ClaimDeviceHostControlsRequest(BaseModel):
    claim_session_id: str = Field(min_length=16, max_length=256)
    limit: int = Field(default=3, ge=1, le=10)
    lease_seconds: int = Field(default=60, ge=10, le=300)


class CompleteDeviceHostControlRequest(BaseModel):
    claim_session_id: str = Field(min_length=16, max_length=256)
    claim_version: int = Field(ge=1)
    status: Literal["succeeded", "failed", "manual_required"]
    result: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] = Field(default_factory=dict)


class DeviceCompletionResult(BaseModel):
    invocation_id: str = Field(min_length=1, max_length=256)
    status: Literal["succeeded", "failed", "cancelled", "timed_out"]
    summary: str = Field(default="", max_length=2000)
    data: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    operation: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
