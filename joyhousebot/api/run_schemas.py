"""Discriminated public submission contract for one top-level Run authority."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"


class StrictRunModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunInput(StrictRunModel):
    type: Literal["message"] = "message"
    content: str = Field(min_length=1)


class AgentExecutionRequest(StrictRunModel):
    mode: Literal["agent"]
    agent_id: str = Field(pattern=_ID_PATTERN)


class TeamExecutionRequest(StrictRunModel):
    mode: Literal["team"]
    team_id: str = Field(pattern=_ID_PATTERN)


class ScenarioExecutionRequest(StrictRunModel):
    mode: Literal["scenario"]
    scenario_id: str = Field(pattern=_ID_PATTERN)
    version: int = Field(ge=1)
    agent_id: str = Field(default="default", pattern=_ID_PATTERN)
    inputs: dict[str, Any] = Field(default_factory=dict)


class WorkflowExecutionRequest(StrictRunModel):
    mode: Literal["workflow"]
    workflow_id: str = Field(pattern=_ID_PATTERN)
    revision_id: str = Field(pattern=_ID_PATTERN)


RunExecutionRequest = Annotated[
    AgentExecutionRequest
    | TeamExecutionRequest
    | ScenarioExecutionRequest
    | WorkflowExecutionRequest,
    Field(discriminator="mode"),
]


class CreateRunRequest(StrictRunModel):
    execution: RunExecutionRequest
    session_id: str | None = Field(default=None, min_length=1, pattern=_ID_PATTERN)
    interaction_mode: Literal["auto", "interactive", "background"] = "auto"
    input: RunInput
    model: str | None = None
    system_prompt: str | None = None
    experiment_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    allowed_tools: list[Annotated[str, Field(pattern=_ID_PATTERN)]] | None = Field(
        default=None, max_length=128
    )
    output_schema: dict[str, Any] | None = None
    verification_policy: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=300.0, gt=0, le=3600)
    max_turns: int | None = Field(default=None, gt=0)
    max_repairs: int | None = Field(default=None, ge=0, le=10)
    max_replans: int | None = Field(default=None, ge=0, le=10)
    input_asset_ids: list[Annotated[str, Field(pattern=_ID_PATTERN)]] = Field(
        default_factory=list, max_length=20
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
