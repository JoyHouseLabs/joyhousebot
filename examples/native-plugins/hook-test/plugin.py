"""Hook Test Plugin - Tests all hook points in joyhousebot.

This plugin registers handlers for all available hooks and logs when they are triggered.
Use it to verify that the hook system is working correctly.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

hook_log: list[dict[str, Any]] = []


def _log(hook_name: str, event_data: dict[str, Any]) -> None:
    entry = {
        "timestamp": datetime.now().isoformat(),
        "hook": hook_name,
        "event": event_data,
    }
    hook_log.append(entry)
    print(f"[HOOK-TEST] {hook_name}: {event_data}")


class HookTestPlugin:
    def register(self, api: Any) -> None:
        from joyhousebot.plugins.hooks.types import (
            BeforeToolCallEvent,
            BeforeToolCallResult,
            AfterToolCallEvent,
            MessageReceivedEvent,
            MessageSendingEvent,
            MessageSendingResult,
            MessageSentEvent,
        )

        @api.on("message_received")
        def on_message_received(event: MessageReceivedEvent, context) -> None:
            _log("message_received", {
                "from_id": event.from_id,
                "content_preview": event.content[:100] if event.content else "",
            })

        @api.on("message_sending", priority=0)
        def on_message_sending(event: MessageSendingEvent, context) -> MessageSendingResult | None:
            _log("message_sending", {
                "to_id": event.to_id,
                "content_preview": event.content[:100] if event.content else "",
            })
            if "__CANCEL__" in event.content:
                return MessageSendingResult(cancel=True)
            if "__REPLACE__" in event.content:
                return MessageSendingResult(content="[REPLACED BY HOOK]")
            return None

        @api.on("message_sent")
        def on_message_sent(event: MessageSentEvent, context) -> None:
            _log("message_sent", {
                "to_id": event.to_id,
                "content_preview": event.content[:100] if event.content else "",
                "success": event.success,
            })

        @api.on("before_tool_call", priority=-100)
        def on_before_tool_call(event: BeforeToolCallEvent, context) -> BeforeToolCallResult | None:
            _log("before_tool_call", {
                "tool_name": event.tool_name,
                "params": str(event.params)[:200] if event.params else {},
            })
            if event.tool_name == "__block_me__":
                return BeforeToolCallResult(
                    block=True,
                    block_reason="Test block: tool __block_me__ is blocked by hook"
                )
            if event.tool_name == "echo" and event.params:
                modified_params = dict(event.params)
                modified_params["_hook_modified"] = True
                return BeforeToolCallResult(params=modified_params)
            return None

        @api.on("after_tool_call")
        def on_after_tool_call(event: AfterToolCallEvent, context) -> None:
            _log("after_tool_call", {
                "tool_name": event.tool_name,
                "success": event.error is None,
                "error": event.error,
                "duration_ms": event.duration_ms,
                "result_preview": str(event.result)[:200] if event.result else None,
            })

        def on_gateway_start(event, context) -> None:
            _log("gateway_start", {"port": getattr(event, "port", None)})

        def on_gateway_stop(event, context) -> None:
            _log("gateway_stop", {"reason": getattr(event, "reason", None)})

        def on_session_start(event, context) -> None:
            _log("session_start", {"session_id": getattr(event, "session_id", None)})

        def on_session_end(event, context) -> None:
            _log("session_end", {
                "session_id": getattr(event, "session_id", None),
                "message_count": getattr(event, "message_count", 0),
            })

        def on_before_agent_start(event, context) -> None:
            _log("before_agent_start", {"prompt_preview": str(getattr(event, "prompt", ""))[:100]})

        def on_agent_end(event, context) -> None:
            _log("agent_end", {
                "success": getattr(event, "success", True),
                "error": getattr(event, "error", None),
            })

        def on_before_compaction(event, context) -> None:
            _log("before_compaction", {"message_count": getattr(event, "message_count", 0)})

        def on_after_compaction(event, context) -> None:
            _log("after_compaction", {"message_count": getattr(event, "message_count", 0)})

        def on_before_reset(event, context) -> None:
            _log("before_reset", {"reason": getattr(event, "reason", None)})

        api.register_hook("gateway_start", on_gateway_start)
        api.register_hook("gateway_stop", on_gateway_stop)
        api.register_hook("session_start", on_session_start)
        api.register_hook("session_end", on_session_end)
        api.register_hook("before_agent_start", on_before_agent_start)
        api.register_hook("agent_end", on_agent_end)
        api.register_hook("before_compaction", on_before_compaction)
        api.register_hook("after_compaction", on_after_compaction)
        api.register_hook("before_reset", on_before_reset)

        def hook_test_status(params: dict[str, Any]) -> dict[str, Any]:
            return {
                "ok": True,
                "hook_count": len(hook_log),
                "hooks": list({entry["hook"] for entry in hook_log}),
                "recent_logs": hook_log[-10:],
            }

        def hook_test_clear(params: dict[str, Any]) -> dict[str, Any]:
            hook_log.clear()
            return {"ok": True, "message": "Hook log cleared"}

        api.register_tool("hook_test_status", hook_test_status)
        api.register_tool("hook_test_clear", hook_test_clear)

        print("[HOOK-TEST] Plugin registered. Hooks are ready.")
        print("[HOOK-TEST] Available tools: hook_test_status, hook_test_clear")
        print("[HOOK-TEST] Test commands:")
        print("[HOOK-TEST]   - Use tool '__block_me__' to test tool blocking")
        print("[HOOK-TEST]   - Use tool 'echo' to test param modification")
        print("[HOOK-TEST]   - Send message with '__CANCEL__' to test message cancellation")
        print("[HOOK-TEST]   - Send message with '__REPLACE__' to test message modification")


plugin = HookTestPlugin()
