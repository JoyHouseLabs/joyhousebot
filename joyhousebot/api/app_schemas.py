"""HTTP request schemas for the App Package control plane."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from joyhousebot.market_protocol.release import APP_ID_PATTERN

_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"


class CreateAppInstallationAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scopes: list[str] = Field(min_length=1, max_length=16)
    expires_at: datetime

    @model_validator(mode="after")
    def validate_expiry(self) -> "CreateAppInstallationAuthorizationRequest":
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        now = datetime.now(timezone.utc)
        expiry = self.expires_at.astimezone(timezone.utc)
        if expiry <= now or expiry > now + timedelta(days=90):
            raise ValueError("expires_at must be within the next 90 days")
        return self


class CreateAppClientRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str = Field(max_length=253, pattern=APP_ID_PATTERN)
    name: str = Field(min_length=1, max_length=160)
    allowed_scopes: list[str] = Field(min_length=1, max_length=16)


class CreateOwnerClientRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=160)
    issuer: str = Field(min_length=3, max_length=500)
    public_key_pem: str = Field(min_length=64, max_length=16_384)
    algorithm: Literal["EdDSA", "RS256"] = "EdDSA"
    allowed_scopes: list[str] = Field(min_length=1, max_length=8)


class RotateOwnerClientKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_key_pem: str = Field(min_length=64, max_length=16_384)
    algorithm: Literal["EdDSA", "RS256"] = "EdDSA"


class UpdateOwnerClientRequest(BaseModel):
    """Replace one first-party product policy as a single audited unit."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    issuer: str = Field(min_length=3, max_length=500)
    public_key_pem: str = Field(min_length=64, max_length=16_384)
    algorithm: Literal["EdDSA", "RS256"] = "EdDSA"
    allowed_scopes: list[str] = Field(min_length=1, max_length=8)


class RegisterAppCallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(min_length=8, max_length=2000)
    secret_ref: str = Field(pattern=r"^env://[A-Za-z_][A-Za-z0-9_]*$")
    events: list[
        Literal["run.completed", "run.failed", "run.cancelled", "run.timed_out"]
    ] = Field(default_factory=list, max_length=4)
    max_attempts: int = Field(default=8, ge=1, le=20)


class SaveAppReleaseRequest(BaseModel):
    manifest: dict[str, Any]


class InstallAppReleaseRequest(BaseModel):
    version: str = Field(min_length=1, max_length=64)
    configuration: dict[str, Any] = Field(default_factory=dict)
    granted_permissions: list[str] = Field(default_factory=list, max_length=128)


class AppInstallationActionRequest(BaseModel):
    action: Literal["activate", "disable", "rollback", "uninstall"]
