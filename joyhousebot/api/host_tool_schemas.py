from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class CreateHostToolRequest(BaseModel):
    claim_session_id: str = Field(min_length=16, max_length=256)
    claim_version: int = Field(ge=1)
    host_request_id: str = Field(min_length=1, max_length=256)
    capability_id: str = Field(min_length=1, max_length=128)
    capability_version: str = Field(min_length=1, max_length=128)
    input: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_size(self) -> "CreateHostToolRequest":
        import json

        if len(json.dumps(self.input, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
            raise ValueError("Host tool input exceeds 1 MiB")
        return self


class IssueHostToolGrantRequest(BaseModel):
    claim_session_id: str = Field(min_length=16, max_length=256)
    claim_version: int = Field(ge=1)
    expires_in_seconds: int = Field(default=300, ge=30, le=3600)


class GrantedHostToolRequest(BaseModel):
    host_request_id: str = Field(min_length=1, max_length=256)
    capability_id: str = Field(min_length=1, max_length=128)
    capability_version: str = Field(min_length=1, max_length=128)
    input: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_size(self) -> "GrantedHostToolRequest":
        import json

        if len(json.dumps(self.input, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
            raise ValueError("Host tool input exceeds 1 MiB")
        return self
