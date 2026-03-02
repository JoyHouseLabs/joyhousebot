"""Plugin invoke tool: call native Python plugin tools."""

from __future__ import annotations

import json
from typing import Any

from joyhousebot.agent.tools.base import Tool


class PluginInvokeTool(Tool):
    """Call a native plugin tool by name."""

    @property
    def name(self) -> str:
        return "plugin_invoke"

    @property
    def description(self) -> str:
        return (
            "Invoke a registered plugin tool by name. "
            "Use 'joyhousebot plugins tools' to see available tools. "
            "Example: tool_name='echo', arguments={'value': 'hello'}"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Tool name (e.g. 'echo', 'greet'). Use 'joyhousebot plugins tools' to see all.",
                },
                "arguments": {
                    "type": "object",
                    "description": "Tool arguments as key-value pairs.",
                    "additionalProperties": True,
                },
            },
            "required": ["tool_name"],
        }

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        from joyhousebot.plugins.manager import get_plugin_manager

        manager = get_plugin_manager()
        out = manager.invoke_tool(name=tool_name or "", args=arguments or {})
        try:
            return json.dumps(out, ensure_ascii=False)
        except (TypeError, ValueError):
            return json.dumps({"ok": False, "error": {"code": "SERIALIZATION_ERROR", "message": str(out)}})
