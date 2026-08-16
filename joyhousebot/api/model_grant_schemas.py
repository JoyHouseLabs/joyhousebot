"""Public control-plane contracts for Device Host model grants."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateHostModelGrantRequest(BaseModel):
    provider_id: str = Field(min_length=1, max_length=128)
    provider_revision_id: str = Field(min_length=1, max_length=256)
    model_id: str = Field(min_length=1, max_length=256)
    token_budget: int = Field(ge=1, le=100_000_000)
    cost_budget_micros: int = Field(default=0, ge=0, le=100_000_000_000)
    max_concurrent: int = Field(default=1, ge=1, le=32)
    expires_in_seconds: int = Field(default=3600, ge=30, le=86_400)
