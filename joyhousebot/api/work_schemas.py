"""Transport DTOs for Work-to-App handoff operations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"


class CreateWorkHandoffRequest(BaseModel):
    installation_id: str = Field(pattern=_ID_PATTERN)
    consumer_id: str = Field(pattern=_ID_PATTERN)
    purpose: str = Field(pattern=_ID_PATTERN)
    work_version: int | None = Field(default=None, ge=1)


class CreateWorkHandoffReceiptRequest(BaseModel):
    status: Literal["accepted", "executing", "verified", "failed"]
    external_reference: str = Field(default="", max_length=512)
    run_id: str = Field(default="", max_length=160)
    summary: str = Field(default="", max_length=4000)
    details: dict[str, Any] = Field(default_factory=dict)
