"""Tests for trace context and TraceRecorder."""

from __future__ import annotations

import pytest

from joyhousebot.services.chat.trace_context import (
    TraceRecorder,
    trace_recorder,
    trace_run_id,
    trace_session_key,
)


def test_trace_recorder_append_and_serialize() -> None:
    rec = TraceRecorder(started_at_ms=1000, message_preview="hi")
    rec.append("tool_start", {"tool": "read_file", "args": {"path": "x"}}, ts_ms=1100)
    rec.append("tool_end", {"tool": "read_file", "result": "content"}, ts_ms=1200)
    rec.set_final("done")
    assert rec.started_at_ms == 1000
    assert rec.message_preview == "hi"
    assert rec.final_content == "done"
    assert rec.tools_used == ["read_file"]
    steps = rec.to_steps_json()
    assert "tool_start" in steps
    assert "tool_end" in steps
    tools_json = rec.to_tools_used_json()
    assert "read_file" in tools_json


def test_trace_context_vars_default_none() -> None:
    assert trace_run_id.get() is None
    assert trace_session_key.get() is None
    assert trace_recorder.get() is None


def test_trace_context_vars_set_and_reset() -> None:
    token_run = trace_run_id.set("run-1")
    token_session = trace_session_key.set("sess:main")
    try:
        assert trace_run_id.get() == "run-1"
        assert trace_session_key.get() == "sess:main"
    finally:
        trace_run_id.reset(token_run)
        trace_session_key.reset(token_session)
    assert trace_run_id.get() is None
    assert trace_session_key.get() is None


def test_trace_recorder_truncates_tool_end_result() -> None:
    """TraceRecorder truncates tool_end result to max_step_payload_chars when set."""
    rec = TraceRecorder(started_at_ms=0, message_preview="", max_step_payload_chars=10)
    long_result = "a" * 50
    rec.append("tool_end", {"tool": "read_file", "result": long_result}, ts_ms=1000)
    assert len(rec.steps) == 1
    step = rec.steps[0]
    assert step["payload"]["result"] == "a" * 10 + "…"
    assert len(step["payload"]["result"]) == 11


def test_trace_recorder_no_truncation_when_none() -> None:
    """When max_step_payload_chars is None, tool_end result is not truncated."""
    rec = TraceRecorder(started_at_ms=0, message_preview="", max_step_payload_chars=None)
    long_result = "x" * 100
    rec.append("tool_end", {"tool": "exec", "result": long_result}, ts_ms=1000)
    assert rec.steps[0]["payload"]["result"] == long_result
