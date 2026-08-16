"""Stable Tool and execution-context API for trusted capability extensions."""

from porthouse.capabilities.tool_adapter import ToolInvocationError, ToolOutput
from porthouse.contracts.capabilities import (
    OperationProgressEvent,
    OperationReconciliationResult,
)
from porthouse.contracts.tools import Tool
from porthouse.domain.capabilities import InvocationStatus
from porthouse.runtime.context import ToolExecutionContext
from porthouse.utils.exceptions import ToolError, tool_error_handler

__all__ = [
    "OperationProgressEvent",
    "OperationReconciliationResult",
    "InvocationStatus",
    "Tool",
    "ToolExecutionContext",
    "ToolError",
    "ToolInvocationError",
    "ToolOutput",
    "tool_error_handler",
]
