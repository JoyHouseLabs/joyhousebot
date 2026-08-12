"""HTTP request schemas for the App Pack control plane."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from joyhousebot.api.run_schemas import RunInput

_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"


class LaunchAppRequest(BaseModel):
    """Narrow user data-plane request for an installed App entrypoint."""

    model_config = ConfigDict(extra="forbid")

    entrypoint_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    session_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    input: RunInput
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional structured inputs accepted only by Scenario entrypoints.",
    )


class CreateAppClientRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=160)
    allowed_scopes: list[str] = Field(min_length=1, max_length=16)


class CreateAppDelegationGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(pattern=_ID_PATTERN)
    scopes: list[str] = Field(min_length=1, max_length=16)
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=30)
    )

    @model_validator(mode="after")
    def validate_expiry(self) -> "CreateAppDelegationGrantRequest":
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        now = datetime.now(timezone.utc)
        expiry = self.expires_at.astimezone(timezone.utc)
        if expiry <= now or expiry > now + timedelta(days=90):
            raise ValueError("App delegation expiry must be within the next 90 days")
        return self


class ExchangeAppTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(pattern=_ID_PATTERN)
    client_secret: str = Field(min_length=20, max_length=512)
    grant_id: str = Field(pattern=_ID_PATTERN)
    scopes: list[str] = Field(min_length=1, max_length=16)
    ttl_seconds: int = Field(default=900, ge=60, le=3600)


class RegisterAppCallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=8, max_length=2000)
    secret_ref: str = Field(pattern=r"^env://[A-Za-z_][A-Za-z0-9_]*$")
    events: list[
        Literal["run.completed", "run.failed", "run.cancelled", "run.timed_out"]
    ] = Field(default_factory=list, max_length=4)
    max_attempts: int = Field(default=8, ge=1, le=20)


class SaveAppPackRequest(BaseModel):
    manifest: dict[str, Any]


class InstallAppPackRequest(BaseModel):
    version: str = Field(min_length=1, max_length=64)
    configuration: dict[str, Any] = Field(default_factory=dict)
    granted_permissions: list[str] = Field(default_factory=list, max_length=128)


class AppPackActionRequest(BaseModel):
    action: Literal["activate", "disable", "rollback", "uninstall"]


class RegisterMarketRequest(BaseModel):
    base_url: str = Field(min_length=8, max_length=1000)
    trusted_root: dict[str, Any]
    discovery: dict[str, Any]
    auth_token_ref: str = Field(default="", max_length=256)
    policy: dict[str, Any] = Field(default_factory=dict)


class AcquireMarketAppRequest(BaseModel):
    registry_id: str = Field(min_length=3, max_length=160)
    publisher_id: str = Field(min_length=3, max_length=160)
    app_id: str = Field(min_length=4, max_length=128)
    version: str | None = Field(default=None, max_length=64)
    channel: Literal["stable", "beta", "security"] = "stable"
    offer_id: str | None = Field(default=None, max_length=160)
    entitlement: dict[str, Any] | None = None


class AcquisitionActionRequest(BaseModel):
    action: Literal["accept", "reject"]


class InstallMarketAcquisitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_grant: dict[str, Any]
    configuration: dict[str, Any] = Field(default_factory=dict)
    granted_permissions: list[str] = Field(default_factory=list, max_length=128)


class SignInstallationReceiptRequest(BaseModel):
    receipt_id: str = Field(
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{7,159}$",
    )
    device_id: str = Field(min_length=1, max_length=128)
    installation_id: str = Field(min_length=3, max_length=160)
    intent_revision: int = Field(ge=1)
    actual_state: Literal["installed", "disabled", "uninstalled", "failed"]
    local_installation_id: str | None = Field(default=None, max_length=160)
    runtime_instance_id: str = Field(min_length=1, max_length=160)
    error_code: str = Field(default="", max_length=100)
    error_message: str = Field(default="", max_length=1000)


class UpdateSubscriptionRequest(BaseModel):
    installation_id: str = Field(min_length=3, max_length=160)
    registry_id: str = Field(min_length=3, max_length=160)
    publisher_id: str = Field(min_length=3, max_length=160)
    app_id: str = Field(min_length=4, max_length=128)
    channel: Literal["stable", "beta", "security"] = "stable"
    version_constraint: str = Field(default="*", max_length=256)
    policy: Literal["notify", "download", "stage", "activate_safe"] = "notify"
    allow_security_patch_download: bool = True
    allow_auto_stage: bool = False
    allow_auto_activate: bool = False
