"""Adapter contract for existing native and MCP tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from joyhousebot.agent.tools.base import Tool
from joyhousebot.contracts import OperationReconciliationResult
from joyhousebot.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
    InvocationStatus,
)


@dataclass(frozen=True, slots=True)
class ToolOutput:
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[dict[str, Any], ...] = ()
    operation: dict[str, Any] | None = None
    summary: str = ""
    status: InvocationStatus = InvocationStatus.SUCCEEDED

    def __post_init__(self) -> None:
        if self.status not in {InvocationStatus.SUCCEEDED, InvocationStatus.ACCEPTED}:
            raise ValueError("ToolOutput status must be succeeded or accepted")
        if self.status == InvocationStatus.ACCEPTED and not self.operation:
            raise ValueError("accepted ToolOutput requires an operation descriptor")


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
        version: str | None = None,
        definition: CapabilityDefinition | None = None,
    ) -> None:
        self.tool = tool
        if definition is not None:
            if (
                definition.ref.kind not in {CapabilityKind.TOOL, CapabilityKind.CONNECTOR}
                or definition.ref.capability_id != tool.name
            ):
                raise ValueError("tool capability definition does not match the adapted tool")
            self.definition = definition
        else:
            safety_metadata_declared = any(
                hasattr(tool, name)
                for name in ("side_effect", "idempotent", "retryable", "data_classification")
            )
            resolved_version = version or (
                "1.1.0" if safety_metadata_declared else "1.0.1"
            )
            self.definition = CapabilityDefinition(
                ref=CapabilityRef(
                    tool.name,
                    resolved_version,
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
                idempotent=bool(getattr(tool, "idempotent", True)),
                retryable=bool(getattr(tool, "retryable", True)),
                side_effect=str(getattr(tool, "side_effect", "none") or "unknown"),
                data_classification=str(
                    getattr(tool, "data_classification", "internal") or "internal"
                ),
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

    @property
    def supports_reconciliation(self) -> bool:
        declared = getattr(self.tool, "supports_reconciliation", None)
        if declared is not None:
            return bool(declared)
        return callable(getattr(self.tool, "reconcile_operation", None))

    async def reconcile_operation(
        self, operation: dict[str, Any], **kwargs: Any
    ) -> OperationReconciliationResult:
        reconcile = getattr(self.tool, "reconcile_operation", None)
        if not callable(reconcile):
            return OperationReconciliationResult(
                status="unknown",
                summary=f"{self.tool.name} does not expose operation reconciliation",
            )
        value = await reconcile(operation=operation, **kwargs)
        if not isinstance(value, OperationReconciliationResult):
            raise TypeError(
                f"tool {self.tool.name} returned unsupported reconciliation "
                f"{type(value).__name__}"
            )
        return value
