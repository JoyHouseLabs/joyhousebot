"""Helpers for chat HTTP endpoint."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException

CHAT_DELTA_THROTTLE_MS = 150


async def build_chat_response(
    *,
    agent: Any,
    message: str,
    session_id: str,
    log_error: Any,
    error_detail: Any,
    config: Any = None,
    check_abort_requested: Any = None,
    on_chat_delta: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Send direct chat message via agent and build response payload. Records trace when trace_run_id is set in context.
    When on_chat_delta is set, calls it with cumulative text during streaming (throttled ~150ms) and once with final text."""
    from joyhousebot.services.chat.trace_context import (
        TraceRecorder,
        trace_recorder,
        trace_run_id,
        trace_session_key,
    )

    execution_stream_callback = None
    run_id = trace_run_id.get()
    session_key = trace_session_key.get()
    if run_id and session_key is not None:
        started_ms = int(time.time() * 1000)
        message_preview = (message or "")[:500].strip() if message else ""
        max_chars = 2000
        if config is not None:
            gw = getattr(config, "gateway", None)
            if gw is not None:
                v = getattr(gw, "trace_max_step_payload_chars", None)
                if v is not None:
                    max_chars = v
        recorder = TraceRecorder(
            started_at_ms=started_ms,
            message_preview=message_preview,
            max_step_payload_chars=max_chars,
        )
        trace_recorder.set(recorder)

        async def _record(etype: str, payload: dict[str, Any]) -> None:
            rec = trace_recorder.get()
            if rec:
                rec.append(etype, payload, ts_ms=int(time.time() * 1000))

        execution_stream_callback = _record

    stream_callback = None
    if on_chat_delta is not None:
        _buf: list[str] = [""]
        _last_sent: list[int] = [0]

        async def _stream_cb(delta: str) -> None:
            _buf[0] += delta
            now_ms = int(time.time() * 1000)
            if now_ms - _last_sent[0] >= CHAT_DELTA_THROTTLE_MS:
                _last_sent[0] = now_ms
                await on_chat_delta(_buf[0])

        stream_callback = _stream_cb

    try:
        response = await agent.process_direct(
            content=message,
            session_key=session_id,
            channel="api",
            chat_id="client",
            stream_callback=stream_callback,
            execution_stream_callback=execution_stream_callback,
            check_abort_requested=check_abort_requested,
        )
        if response is None:
            return {
                "ok": False,
                "aborted": True,
                "session_id": session_id,
            }
        if on_chat_delta is not None:
            await on_chat_delta(response)
        if execution_stream_callback:
            rec = trace_recorder.get()
            if rec:
                rec.set_final(response)
        return {
            "ok": True,
            "response": response,
            "session_id": session_id,
        }
    except Exception as e:
        log_error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=error_detail(e))

