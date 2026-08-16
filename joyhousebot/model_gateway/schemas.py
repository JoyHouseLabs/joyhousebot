"""Bounded request contracts for the Host Model Gateway."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ModelGatewayMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = Field(default=None, max_length=128)
    tool_call_id: str | None = Field(default=None, max_length=256)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list, max_length=128)


class HostModelChatRequest(BaseModel):
    request_id: str = Field(min_length=16, max_length=256)
    model: str = Field(min_length=1, max_length=256)
    messages: list[ModelGatewayMessage] = Field(min_length=1, max_length=500)
    tools: list[dict[str, Any]] = Field(default_factory=list, max_length=128)
    max_tokens: int = Field(default=4096, ge=1, le=1_000_000)
    temperature: float = Field(default=0.3, ge=0, le=2)

    @model_validator(mode="after")
    def validate_payload_size(self) -> "HostModelChatRequest":
        import json

        size = len(
            json.dumps(
                self.model_dump(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if size > 1024 * 1024:
            raise ValueError("model gateway request exceeds 1 MiB")
        return self


class OpenAIChatCompletionRequest(BaseModel):
    """Bounded OpenAI-compatible surface used by SDKs such as Pi."""

    model: str = Field(min_length=1, max_length=256)
    messages: list[ModelGatewayMessage] = Field(min_length=1, max_length=500)
    tools: list[dict[str, Any]] = Field(default_factory=list, max_length=128)
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    temperature: float = Field(default=0.3, ge=0, le=2)
    stream: bool = False

    @model_validator(mode="after")
    def validate_payload(self) -> "OpenAIChatCompletionRequest":
        import json

        if self.max_tokens is not None and self.max_completion_tokens is not None:
            raise ValueError("use either max_tokens or max_completion_tokens")
        size = len(
            json.dumps(
                self.model_dump(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if size > 1024 * 1024:
            raise ValueError("model gateway request exceeds 1 MiB")
        return self
