"""Tests for traces RPC methods (traces.list, traces.get)."""

from __future__ import annotations

import pytest

from joyhousebot.api.rpc.traces_methods import try_handle_traces_method


@pytest.mark.asyncio
async def test_traces_list_empty() -> None:
    def get_store():
        store = type("Store", (), {})()
        store.list_agent_traces = lambda **kw: ([], None)
        return store

    result = await try_handle_traces_method(
        method="traces.list",
        params={},
        get_store=get_store,
        rpc_error=lambda c, m, d: {"code": c, "message": m},
    )
    assert result is not None
    ok, payload, err = result
    assert ok is True
    assert err is None
    assert payload is not None
    assert "items" in payload
    assert payload["items"] == []


@pytest.mark.asyncio
async def test_traces_get_missing() -> None:
    def get_store():
        store = type("Store", (), {})()
        store.get_agent_trace = lambda tid: None
        return store

    result = await try_handle_traces_method(
        method="traces.get",
        params={"traceId": "nonexistent"},
        get_store=get_store,
        rpc_error=lambda c, m, d: {"code": c, "message": m},
    )
    assert result is not None
    ok, payload, err = result
    assert ok is False
    assert err is not None
    assert err.get("code") == "NOT_FOUND"


@pytest.mark.asyncio
async def test_traces_get_success() -> None:
    def get_store():
        store = type("Store", (), {})()
        store.get_agent_trace = lambda tid: {
            "traceId": tid,
            "sessionKey": "sess:main",
            "status": "ok",
            "startedAtMs": 1000,
            "endedAtMs": 2000,
            "errorText": None,
            "stepsJson": "[]",
            "toolsUsed": "[]",
            "messagePreview": "hi",
        } if tid == "run-1" else None
        return store

    result = await try_handle_traces_method(
        method="traces.get",
        params={"traceId": "run-1"},
        get_store=get_store,
        rpc_error=lambda c, m, d: {"code": c, "message": m},
    )
    assert result is not None
    ok, payload, err = result
    assert ok is True
    assert err is None
    assert payload is not None
    assert payload["traceId"] == "run-1"
    assert payload["sessionKey"] == "sess:main"


@pytest.mark.asyncio
async def test_traces_unknown_method_returns_none() -> None:
    result = await try_handle_traces_method(
        method="other.method",
        params={},
        get_store=lambda: None,
        rpc_error=lambda *_: {},
    )
    assert result is None
