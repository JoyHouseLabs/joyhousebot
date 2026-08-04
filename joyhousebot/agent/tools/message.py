"""Message tool for sending messages to users."""

from typing import Any, Awaitable, Callable

from joyhousebot.agent.tools.base import Tool
from joyhousebot.bus.events import OutboundMessage
from joyhousebot.capabilities.tool_adapter import ToolInvocationError
from joyhousebot.runtime.context import ToolExecutionContext


class MessageTool(Tool):
    """Tool to send messages to users on chat channels."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
    ):
        self._send_callback = send_callback

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return "Send a message to the user. Use this when you want to communicate something."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The message content to send"},
            },
            "required": ["content"],
        }

    async def execute(self, content: str, **kwargs: Any) -> str:
        tool_context = kwargs.get("tool_context")
        if not isinstance(tool_context, ToolExecutionContext):
            raise ToolInvocationError("CONTEXT_REQUIRED", "Message tool requires run context")
        channel = tool_context.channel
        chat_id = tool_context.chat_id
        if not channel or not chat_id:
            raise ToolInvocationError("DELIVERY_TARGET_REQUIRED", "Run context has no delivery target")

        if not self._send_callback:
            raise ToolInvocationError("CAPABILITY_UNAVAILABLE", "Message sending not configured")

        msg = OutboundMessage(channel=channel, chat_id=chat_id, content=content)

        try:
            await self._send_callback(msg)
            return f"Message sent to {channel}:{chat_id}"
        except Exception as e:
            raise ToolInvocationError("MESSAGE_SEND_FAILED", str(e), retryable=True) from e
