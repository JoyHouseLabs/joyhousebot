"""Spawn tool for creating background subagents."""

from typing import TYPE_CHECKING, Any

from joyhousebot.agent.tools.base import Tool
from joyhousebot.capabilities.tool_adapter import ToolInvocationError, ToolOutput
from joyhousebot.contracts import OperationReconciliationResult
from joyhousebot.runtime.context import ToolExecutionContext

if TYPE_CHECKING:
    from joyhousebot.agent.subagent import SubagentManager


class SpawnTool(Tool):
    """Submit a durable child Agent run to the distributed runtime."""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager

    side_effect = "internal"
    idempotent = True
    retryable = True

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Submit a durable child Agent run to handle a task. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "Its status and result are linked to the parent workflow."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the subagent to complete",
                },
                "label": {
                    "type": "string",
                    "description": "Optional short label for the task (for display)",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional registered specialist Agent id",
                },
                "output_schema": {
                    "type": "object",
                    "description": "Optional JSON Schema required from the child Agent",
                },
            },
            "required": ["task"],
        }

    async def execute(
        self,
        task: str,
        label: str | None = None,
        agent_id: str | None = None,
        output_schema: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolOutput:
        """Spawn a subagent to execute the given task."""
        tool_context = kwargs.get("tool_context")
        if not isinstance(tool_context, ToolExecutionContext):
            raise ToolInvocationError("CONTEXT_REQUIRED", "Spawn tool requires run context")
        return await self._manager.spawn(
            task=task,
            label=label,
            agent_id=agent_id,
            output_schema=output_schema,
            origin_channel=tool_context.channel,
            origin_chat_id=tool_context.chat_id,
            idempotency_key=tool_context.idempotency_key,
        )

    async def reconcile_operation(
        self, operation: dict[str, Any], **kwargs: Any
    ) -> OperationReconciliationResult:
        tool_context = kwargs.get("tool_context")
        if not isinstance(tool_context, ToolExecutionContext):
            return OperationReconciliationResult(
                status="unknown", summary="Spawn reconciliation requires run context"
            )
        return await self._manager.reconcile(operation, user_id=tool_context.user_id)
