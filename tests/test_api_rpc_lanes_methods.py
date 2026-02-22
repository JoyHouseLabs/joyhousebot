"""Tests for lanes RPC methods (lanes.status, lanes.list)."""

from __future__ import annotations

import pytest

from joyhousebot.api.rpc.lanes_methods import try_handle_lanes_method


@pytest.fixture
def app_state():
    return {"rpc_session_to_run_id": {}, "rpc_lane_pending": {}}


@pytest.mark.asyncio
async def test_lanes_list_empty(app_state):
    ok, payload, err = await try_handle_lanes_method(
        method="lanes.list",
        params={},
        app_state=app_state,
        now_ms=lambda: 1000,
        rpc_error=lambda c, m, d: {"code": c, "message": m},
    )
    assert ok is True
    assert err is None
    assert "lanes" in payload
    assert payload["lanes"] == []


@pytest.mark.asyncio
async def test_lanes_status_global(app_state):
    app_state["rpc_session_to_run_id"]["main"] = "run-1"
    ok, payload, err = await try_handle_lanes_method(
        method="lanes.status",
        params={},
        app_state=app_state,
        now_ms=lambda: 2000,
        rpc_error=lambda c, m, d: {"code": c, "message": m},
    )
    assert ok is True
    assert err is None
    assert "summary" in payload
    assert payload["summary"]["runningSessions"] == 1
    assert "lanes" in payload


@pytest.mark.asyncio
async def test_lanes_status_single(app_state):
    app_state["rpc_session_to_run_id"]["s1"] = "r1"
    app_state["rpc_lane_pending"]["s1"] = [
        {"runId": "r2", "sessionKey": "s1", "enqueuedAt": 500, "params": {}},
    ]
    ok, payload, err = await try_handle_lanes_method(
        method="lanes.status",
        params={"sessionKey": "s1"},
        app_state=app_state,
        now_ms=lambda: 1000,
        rpc_error=lambda c, m, d: {"code": c, "message": m},
    )
    assert ok is True
    assert payload["sessionKey"] == "s1"
    assert payload["runningRunId"] == "r1"
    assert payload["queued"] == 1
    assert payload["headWaitMs"] == 500


@pytest.mark.asyncio
async def test_lanes_unrelated_method(app_state):
    out = await try_handle_lanes_method(
        method="chat.send",
        params={},
        app_state=app_state,
        now_ms=lambda: 0,
        rpc_error=lambda c, m, d: None,
    )
    assert out is None
