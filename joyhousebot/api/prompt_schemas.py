"""Strict control-plane request schemas for versioned Prompt assets."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"


class _StrictPromptModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SavePromptDraftRequest(_StrictPromptModel):
    prompt_id: str = Field(pattern=_ID_PATTERN)
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    content: str = Field(min_length=1, max_length=200_000)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=32)
    change_note: str = Field(default="", max_length=2000)


class BindPromptRevisionRequest(_StrictPromptModel):
    target_type: Literal["agent"] = "agent"
    target_id: str = Field(pattern=_ID_PATTERN)
    target_revision_id: str = Field(pattern=_ID_PATTERN)
    prompt_revision_id: str = Field(pattern=_ID_PATTERN)
    purpose: Literal["system_instruction"] = "system_instruction"
    position: int = Field(default=100, ge=0, le=10000)
    enabled: bool = True
