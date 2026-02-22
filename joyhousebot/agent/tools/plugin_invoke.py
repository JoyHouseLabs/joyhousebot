"""Plugin invoke tool: unified bridge for Agent to call Native/Bridge plugin tools."""

from __future__ import annotations

import json
from typing import Any

from joyhousebot.agent.tools.base import Tool


class PluginInvokeTool(Tool):
    """Call a plugin tool by plugin_id and tool_name. Uses plugin.invoke contract."""

    @property
    def name(self) -> str:
        return "plugin.invoke"

    @property
    def description(self) -> str:
        return (
            "Invoke a plugin tool. Use when a skill or user asks to use plugin capabilities "
            "(e.g. library.create_book, library.list_books). Provide plugin_id, tool_name, and optional arguments."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plugin_id": {
                    "type": "string",
                    "description": "Plugin ID (e.g. library, native.hello)",
                },
                "tool_name": {
                    "type": "string",
                    "description": "Tool name as registered (e.g. create_book or library.create_book)",
                },
                "arguments": {
                    "type": "object",
                    "description": "Key-value arguments for the tool",
                    "additionalProperties": True,
                },
            },
            "required": ["plugin_id", "tool_name"],
        }

    async def execute(
        self,
        plugin_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        from joyhousebot.plugins.manager import get_plugin_manager

        manager = get_plugin_manager()
        out = manager.invoke_plugin_tool(
            plugin_id=plugin_id or "",
            tool_name=tool_name or "",
            arguments=arguments,
        )
        try:
            return json.dumps(out, ensure_ascii=False)
        except (TypeError, ValueError):
            return json.dumps({"ok": False, "error": {"code": "SERIALIZATION_ERROR", "message": str(out)}})
