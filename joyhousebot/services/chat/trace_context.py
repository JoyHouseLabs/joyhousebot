"""Context and recorder for agent run traces (observability)."""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any

# Context vars set by chat_service before calling chat(), read by build_chat_response.
trace_run_id: ContextVar[str | None] = ContextVar("trace_run_id", default=None)
trace_session_key: ContextVar[str | None] = ContextVar("trace_session_key", default=None)
trace_recorder: ContextVar["TraceRecorder | None"] = ContextVar("trace_recorder", default=None)

class TraceRecorder:
    """Mutable recorder for one agent run; append steps from execution_stream_callback."""

    __slots__ = ("started_at_ms", "steps", "final_content", "tools_used", "message_preview", "max_step_payload_chars")

    def __init__(
        self,
        started_at_ms: int,
        message_preview: str | None = None,
        max_step_payload_chars: int | None = 2000,
    ):
        self.started_at_ms = started_at_ms
        self.steps: list[dict[str, Any]] = []
        self.final_content: str | None = None
        self.tools_used: list[str] = []
        self.message_preview = message_preview or ""
        self.max_step_payload_chars = max_step_payload_chars

    def append(self, etype: str, payload: dict[str, Any], ts_ms: int) -> None:
        step: dict[str, Any] = {"type": etype, "payload": payload, "ts_ms": ts_ms}
        if etype == "tool_start" and payload.get("tool"):
            tool_name = payload.get("tool")
            if isinstance(tool_name, str) and tool_name not in self.tools_used:
                self.tools_used.append(tool_name)
        if etype == "tool_end" and "result" in payload:
            result = payload.get("result")
            if (
                isinstance(result, str)
                and self.max_step_payload_chars is not None
                and len(result) > self.max_step_payload_chars
            ):
                payload = {**payload, "result": result[: self.max_step_payload_chars] + "…"}
                step["payload"] = payload
        self.steps.append(step)

    def set_final(self, content: str | None) -> None:
        self.final_content = content

    def to_steps_json(self) -> str:
        return json.dumps(self.steps, ensure_ascii=False)

    def to_tools_used_json(self) -> str:
        return json.dumps(self.tools_used, ensure_ascii=False)
