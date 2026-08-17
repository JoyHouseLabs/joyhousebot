"""Versioned API schemas for personal and App Entry Point schedules."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"


class ScheduleSpec(BaseModel):
    kind: Literal["at", "every", "cron"]
    at_ms: int | None = None
    every_ms: int | None = Field(default=None, ge=60000)
    cron_expr: str | None = None
    timezone: str | None = None


class SchedulePayload(BaseModel):
    kind: Literal["agent_turn", "agent_monitor", "app_entrypoint"] = "agent_turn"
    message: str = Field(default="", max_length=20_000)
    deliver: bool = False
    channel: str | None = Field(default=None, max_length=32)
    to: str | None = Field(default=None, max_length=128)
    session_mode: Literal["isolated", "main"] = "isolated"
    session_id: str | None = Field(default=None, min_length=1, pattern=_ID_PATTERN)
    quiet_token: str = Field(default="NO_ACTION", min_length=1, max_length=64)
    defer_when_busy: bool = True
    busy_backoff_ms: int = Field(default=60_000, ge=1_000, le=3_600_000)
    preflight_mode: Literal["always", "runtime_attention"] = "always"
    context_mode: Literal["full", "light"] = "full"
    active_hours: dict[str, str] | None = None
    entrypoint_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=_ID_PATTERN
    )
    inputs: dict[str, Any] | None = None

    @model_validator(mode="after")
    def normalize_monitor_session(self) -> "SchedulePayload":
        if self.kind == "agent_monitor" and self.session_mode == "main":
            self.session_id = self.session_id or "main"
        if self.kind != "agent_monitor" and self.preflight_mode != "always":
            raise ValueError("preflight_mode is only available for agent_monitor")
        if self.kind != "agent_monitor" and self.context_mode != "full":
            raise ValueError("context_mode is only available for agent_monitor")
        if self.kind != "agent_monitor" and self.active_hours is not None:
            raise ValueError("active_hours is only available for agent_monitor")
        if self.kind != "app_entrypoint" and self.entrypoint_id is not None:
            raise ValueError("entrypoint_id is only available for app_entrypoint")
        if self.kind != "app_entrypoint" and self.inputs is not None:
            raise ValueError("inputs are only available for app_entrypoint")
        if self.kind != "app_entrypoint" and not self.message.strip():
            raise ValueError("message is required")
        if self.kind == "app_entrypoint" and self.session_mode != "isolated":
            raise ValueError("app_entrypoint schedules use a derived session")
        if self.active_hours is not None:
            from porthouse.cron.active_hours import normalize_active_hours

            self.active_hours = normalize_active_hours(self.active_hours)
        if not self.quiet_token.strip():
            raise ValueError("quiet_token must contain a non-whitespace character")
        self.quiet_token = self.quiet_token.strip()
        return self


class SchedulePolicy(BaseModel):
    max_submit_attempts: int = Field(default=3, ge=1, le=10)
    max_run_retries: int = Field(default=0, ge=0, le=10)
    retry_backoff_ms: int = Field(default=60_000, ge=1_000, le=3_600_000)
    misfire_policy: Literal["fire_once", "skip"] = "fire_once"
    misfire_grace_ms: int = Field(default=300_000, ge=0, le=86_400_000)
    overlap_policy: Literal["serialize", "skip"] = "serialize"


class CreateScheduleRequest(BaseModel):
    name: str = Field(min_length=1)
    agent_id: str = Field(default="default", pattern=_ID_PATTERN)
    schedule: ScheduleSpec
    payload: SchedulePayload
    policy: SchedulePolicy = Field(default_factory=SchedulePolicy)
    enabled: bool = True


class CreateAppScheduleRequest(BaseModel):
    """Schedule creation scoped to one App installation's Entry Point."""

    name: str = Field(min_length=1)
    schedule: ScheduleSpec
    payload: SchedulePayload
    policy: SchedulePolicy = Field(default_factory=SchedulePolicy)
    enabled: bool = True

    @model_validator(mode="after")
    def require_app_entrypoint(self) -> "CreateAppScheduleRequest":
        if self.payload.kind != "app_entrypoint":
            raise ValueError("App schedules must use payload.kind=app_entrypoint")
        return self


class UpdateScheduleRequest(BaseModel):
    name: str | None = None
    agent_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    schedule: ScheduleSpec | None = None
    payload: SchedulePayload | None = None
    policy: SchedulePolicy | None = None
    enabled: bool | None = None


class UpdateMonitorScratchRequest(BaseModel):
    content: str = Field(max_length=16384)
    expected_revision: int = Field(ge=0)
