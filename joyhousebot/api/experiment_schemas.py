"""Strict control-plane schemas for online revision experiments."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"


class _StrictExperimentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExperimentVariantRequest(_StrictExperimentModel):
    variant_id: str = Field(pattern=_ID_PATTERN)
    target_id: str = Field(pattern=_ID_PATTERN)
    target_revision_id: str = Field(pattern=_ID_PATTERN)
    weight_basis_points: int = Field(gt=0, le=10_000)


class SaveExperimentRequest(_StrictExperimentModel):
    experiment_id: str = Field(pattern=_ID_PATTERN)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    target_type: Literal["agent"] = "agent"
    traffic_basis_points: int = Field(ge=0, le=10_000)
    variants: list[ExperimentVariantRequest] = Field(min_length=2, max_length=16)
    guardrails: dict = Field(default_factory=dict)


class SetExperimentStatusRequest(_StrictExperimentModel):
    status: Literal["running", "paused", "stopped"]
    reason: str = Field(default="", max_length=2000)
