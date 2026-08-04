"""Adapter contract for existing native and MCP tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from joyhousebot.agent.tools.base import Tool
from joyhousebot.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
)


@dataclass(frozen=True, slots=True)
class ToolOutput:
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[dict[str, Any], ...] = ()
    operation: dict[str, Any] | None = None
    summary: str = ""


class ToolInvocationError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ToolCapabilityAdapter:
    def __init__(
        self,
        tool: Tool,
        *,
        version: str = "1.0.1",
        definition: CapabilityDefinition | None = None,
    ) -> None:
        self.tool = tool
        if definition is not None:
            if definition.ref.kind != CapabilityKind.TOOL or definition.ref.capability_id != tool.name:
                raise ValueError("tool capability definition does not match the adapted tool")
            self.definition = definition
        else:
            self.definition = CapabilityDefinition(
                ref=CapabilityRef(
                    tool.name,
                    version,
                    CapabilityKind.TOOL,
                    "joyhousebot.core",
                    "0.1.2",
                    "builtin",
                ),
                name=tool.name,
                description=tool.description,
                input_schema=tool.parameters,
                output_schema={"type": "object"},
                adapter=f"tool:{tool.__class__.__module__}.{tool.__class__.__name__}",
                timeout_seconds=max(1, int(getattr(tool, "timeout", 60) or 60)),
            )

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        try:
            value = await self.tool.execute(**inputs, **kwargs)
        except ToolInvocationError:
            raise
        except Exception as exc:
            from joyhousebot.utils.exceptions import ToolError, ValidationError

            if isinstance(exc, ValidationError):
                raise ToolInvocationError("INVALID_PARAMETERS", str(exc)) from exc
            if isinstance(exc, ToolError):
                raise ToolInvocationError("TOOL_EXECUTION_FAILED", str(exc)) from exc
            raise
        if isinstance(value, ToolOutput):
            return value
        if isinstance(value, str):
            return ToolOutput(content=value, data={"content": value})
        raise TypeError(f"tool {self.tool.name} returned unsupported output {type(value).__name__}")
