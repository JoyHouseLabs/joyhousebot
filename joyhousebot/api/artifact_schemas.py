"""HTTP schemas for scoped Host Artifact uploads."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateArtifactUploadGrantRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=500)
    media_type: str = Field(min_length=3, max_length=200)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=0)
    expires_in_seconds: int = Field(default=900, ge=60, le=3600)
    provenance: dict[str, Any] = Field(default_factory=dict)
