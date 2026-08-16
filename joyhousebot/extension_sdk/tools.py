"""Stable Tool and execution-context API for trusted capability extensions."""

from joyhousebot.capabilities.tool_adapter import ToolInvocationError, ToolOutput
from joyhousebot.contracts.capabilities import (
    OperationProgressEvent,
    OperationReconciliationResult,
)
from joyhousebot.contracts.tools import Tool
from joyhousebot.domain.capabilities import InvocationStatus
from joyhousebot.runtime.context import ToolExecutionContext
from joyhousebot.utils.exceptions import ToolError, tool_error_handler

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
