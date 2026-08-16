"""Transport schemas for the independent Skill control plane."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SaveSkillDraftRequest(BaseModel):
    skill_id: str = Field(pattern=r"^skill\.[A-Za-z0-9][A-Za-z0-9_.:-]{0,121}$")
    version: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    instruction_content: str = Field(default="", max_length=200000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: list[dict[str, str]] = Field(
        default_factory=list, max_length=64
    )
    required_integrations: list[str] = Field(default_factory=list, max_length=32)
    examples: list[dict[str, Any]] = Field(default_factory=list, max_length=24)
    eval_cases: list[dict[str, Any]] = Field(default_factory=list, max_length=64)
    templates: list[dict[str, Any]] = Field(default_factory=list, max_length=24)
    change_note: str = Field(default="", max_length=2000)
    source: dict[str, Any] = Field(default_factory=dict)


class SetSkillStatusRequest(BaseModel):
    status: Literal["active", "disabled", "archived"]
