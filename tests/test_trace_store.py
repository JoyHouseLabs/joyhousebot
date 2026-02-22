"""Tests for agent trace storage (sqlite_store agent_traces)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from joyhousebot.storage.sqlite_store import LocalStateStore


@pytest.fixture
def store(tmp_path: Path) -> LocalStateStore:
    return LocalStateStore(tmp_path / "trace_test.db")


def test_insert_and_get_agent_trace(store: LocalStateStore) -> None:
    store.insert_agent_trace(
        trace_id="run-1",
        session_key="sess:main",
        status="ok",
        started_at_ms=1000,
        ended_at_ms=2000,
        error_text=None,
        steps_json='[{"type":"tool_start","payload":{},"ts_ms":1500}]',
        tools_used='["read_file"]',
        message_preview="hello",
    )
    trace = store.get_agent_trace("run-1")
    assert trace is not None
    assert trace["traceId"] == "run-1"
    assert trace["sessionKey"] == "sess:main"
    assert trace["status"] == "ok"
    assert trace["startedAtMs"] == 1000
    assert trace["endedAtMs"] == 2000
    assert trace["errorText"] is None
    assert "tool_start" in trace["stepsJson"]
    assert trace["messagePreview"] == "hello"


def test_get_agent_trace_missing(store: LocalStateStore) -> None:
    assert store.get_agent_trace("nonexistent") is None


def test_list_agent_traces_empty(store: LocalStateStore) -> None:
    items, cursor = store.list_agent_traces(limit=10)
    assert items == []
    assert cursor is None


def test_list_agent_traces_with_data(store: LocalStateStore) -> None:
    for i in range(5):
        store.insert_agent_trace(
            trace_id=f"run-{i}",
            session_key="sess:main",
            status="ok",
            started_at_ms=1000 + i,
            ended_at_ms=2000 + i,
            error_text=None,
            steps_json="[]",
            tools_used="[]",
            message_preview=None,
        )
    items, cursor = store.list_agent_traces(limit=3)
    assert len(items) == 3
    assert items[0]["traceId"] == "run-4"
    assert items[1]["traceId"] == "run-3"
    assert cursor is not None
    items2, cursor2 = store.list_agent_traces(limit=5, cursor=cursor)
    assert len(items2) == 2
    assert cursor2 is None


def test_list_agent_traces_by_session(store: LocalStateStore) -> None:
    store.insert_agent_trace(
        trace_id="run-a",
        session_key="sess:one",
        status="ok",
        started_at_ms=1000,
        ended_at_ms=2000,
        error_text=None,
        steps_json="[]",
        tools_used="[]",
        message_preview=None,
    )
    store.insert_agent_trace(
        trace_id="run-b",
        session_key="sess:two",
        status="ok",
        started_at_ms=1001,
        ended_at_ms=2001,
        error_text=None,
        steps_json="[]",
        tools_used="[]",
        message_preview=None,
    )
    items, _ = store.list_agent_traces(session_key="sess:one", limit=10)
    assert len(items) == 1
    assert items[0]["traceId"] == "run-a"
