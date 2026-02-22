"""Tests for lane queue service and observability."""

from __future__ import annotations

import pytest

from joyhousebot.services.lanes import (
    lane_can_run,
    lane_dequeue_next,
    lane_enqueue,
    lane_list_all,
    lane_status,
)


@pytest.fixture
def app_state():
    """Fresh app_state with rpc_session_to_run_id and rpc_lane_pending."""
    return {
        "rpc_session_to_run_id": {},
        "rpc_lane_pending": {},
    }


def test_lane_can_run_empty(app_state):
    assert lane_can_run(app_state, "main") is True
    assert lane_can_run(app_state, "s1") is True


def test_lane_can_run_after_register(app_state):
    app_state["rpc_session_to_run_id"]["main"] = "run-1"
    assert lane_can_run(app_state, "main") is False
    assert lane_can_run(app_state, "other") is True


def test_lane_enqueue_queued(app_state):
    now = 1000
    result = lane_enqueue(app_state, "main", "run-1", {"message": "hi", "sessionKey": "main"}, now)
    assert result["status"] == "queued"
    assert result["position"] == 1
    assert result["queueDepth"] == 1
    assert app_state["rpc_lane_pending"]["main"] == [
        {"runId": "run-1", "sessionKey": "main", "enqueuedAt": 1000, "params": {"message": "hi", "sessionKey": "main"}},
    ]


def test_lane_enqueue_rejected_when_full(app_state):
    now = 1000
    for i in range(100):
        lane_enqueue(app_state, "main", f"run-{i}", {"message": f"m{i}", "sessionKey": "main"}, now + i)
    result = lane_enqueue(app_state, "main", "run-100", {"message": "m100", "sessionKey": "main"}, 2000)
    assert result["status"] == "rejected"
    assert len(app_state["rpc_lane_pending"]["main"]) == 100


def test_lane_dequeue_next_empty(app_state):
    assert lane_dequeue_next(app_state, "main") is None


def test_lane_dequeue_next_fifo(app_state):
    lane_enqueue(app_state, "main", "run-1", {"message": "a", "sessionKey": "main"}, 1000)
    lane_enqueue(app_state, "main", "run-2", {"message": "b", "sessionKey": "main"}, 1001)
    first = lane_dequeue_next(app_state, "main")
    assert first is not None
    assert first["runId"] == "run-1"
    assert first["params"]["message"] == "a"
    second = lane_dequeue_next(app_state, "main")
    assert second is not None
    assert second["runId"] == "run-2"
    assert lane_dequeue_next(app_state, "main") is None
    assert "main" not in app_state["rpc_lane_pending"]


def test_lane_status_single(app_state):
    app_state["rpc_session_to_run_id"]["main"] = "run-x"
    lane_enqueue(app_state, "main", "run-2", {"message": "m", "sessionKey": "main"}, 500)
    lane_enqueue(app_state, "main", "run-3", {"message": "m2", "sessionKey": "main"}, 600)
    st = lane_status(app_state, "main", 1000)
    assert st["sessionKey"] == "main"
    assert st["runningRunId"] == "run-x"
    assert st["queued"] == 2
    assert st["queueDepth"] == 2
    assert st["headWaitMs"] == 500
    assert st["oldestEnqueuedAt"] == 500


def test_lane_status_global(app_state):
    app_state["rpc_session_to_run_id"]["a"] = "r1"
    lane_enqueue(app_state, "a", "r2", {"message": "m", "sessionKey": "a"}, 100)
    lane_enqueue(app_state, "b", "r3", {"message": "m", "sessionKey": "b"}, 200)
    st = lane_status(app_state, None, 300)
    assert "summary" in st
    assert st["summary"]["runningSessions"] == 1
    assert st["summary"]["queuedSessions"] == 2
    assert st["summary"]["totalQueued"] == 2
    assert len(st["lanes"]) == 2


def test_lane_list_all(app_state):
    app_state["rpc_session_to_run_id"]["s1"] = "run-1"
    lane_enqueue(app_state, "s2", "run-2", {"message": "m", "sessionKey": "s2"}, 100)
    lanes = lane_list_all(app_state, 200)
    assert len(lanes) == 2
    by_key = {r["sessionKey"]: r for r in lanes}
    assert by_key["s1"]["runningRunId"] == "run-1"
    assert by_key["s1"]["queued"] == 0
    assert by_key["s2"]["runningRunId"] is None
    assert by_key["s2"]["queued"] == 1
    assert by_key["s2"]["headWaitMs"] == 100
