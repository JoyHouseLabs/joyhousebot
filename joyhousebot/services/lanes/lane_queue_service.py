"""
Lane queue: per-session strict FIFO for chat.send/agent.

State machine:
- running: at most one run per session (rpc_session_to_run_id).
- pending: list of enqueued items per session (rpc_lane_pending).
- Response semantics: "started" (run now), "queued" (enqueued; runId + position).

Observability: running run, queued count, queueDepth, headWaitMs (oldest enqueued).
"""

from __future__ import annotations

from typing import Any

# Keys in app_state for lane queue state
KEY_PENDING = "rpc_lane_pending"  # session_key -> list of {runId, sessionKey, enqueuedAt, params}
# rpc_session_to_run_id (existing) = session_key -> running run_id

# Default cap per lane to avoid unbounded memory
DEFAULT_MAX_PENDING_PER_LANE = 100


def _get_pending(app_state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out = app_state.get(KEY_PENDING)
    if not isinstance(out, dict):
        app_state[KEY_PENDING] = {}
        return app_state[KEY_PENDING]
    return out


def _get_session_to_run(app_state: dict[str, Any]) -> dict[str, str]:
    out = app_state.get("rpc_session_to_run_id")
    if not isinstance(out, dict):
        app_state["rpc_session_to_run_id"] = {}
        return app_state["rpc_session_to_run_id"]
    return out


def lane_can_run(app_state: dict[str, Any], session_key: str) -> bool:
    """True if this session has no running job (can start immediately)."""
    session_to_run = _get_session_to_run(app_state)
    return session_key not in session_to_run


def lane_enqueue(
    app_state: dict[str, Any],
    session_key: str,
    run_id: str,
    params: dict[str, Any],
    now_ms: int,
    max_pending_per_lane: int = DEFAULT_MAX_PENDING_PER_LANE,
) -> dict[str, Any]:
    """
    Enqueue a request for the session. Params must include at least message/sessionKey for replay.
    Returns {"status": "queued", "position": 1-based, "queueDepth": N} or {"status": "rejected"}.
    """
    pending = _get_pending(app_state)
    queue = pending.setdefault(session_key, [])
    if len(queue) >= max_pending_per_lane:
        return {"status": "rejected"}
    item = {
        "runId": run_id,
        "sessionKey": session_key,
        "enqueuedAt": now_ms,
        "params": dict(params),
    }
    queue.append(item)
    return {"status": "queued", "position": len(queue), "queueDepth": len(queue)}


def lane_dequeue_next(app_state: dict[str, Any], session_key: str) -> dict[str, Any] | None:
    """Pop and return the next pending item for this session, or None."""
    pending = _get_pending(app_state)
    queue = pending.get(session_key)
    if not queue:
        return None
    item = queue.pop(0)
    if not queue:
        del pending[session_key]
    return item


def lane_status(
    app_state: dict[str, Any],
    session_key: str | None,
    now_ms: int,
) -> dict[str, Any]:
    """
    Single-lane status for lanes.status RPC.
    If session_key is None, returns summary for all lanes.
    """
    session_to_run = _get_session_to_run(app_state)
    pending = _get_pending(app_state)
    if session_key is not None:
        running_run_id = session_to_run.get(session_key)
        queue = pending.get(session_key, [])
        head_wait_ms: int | None = None
        if queue and now_ms > 0:
            head = queue[0]
            enq = head.get("enqueuedAt")
            if enq is not None:
                head_wait_ms = now_ms - int(enq)
        return {
            "sessionKey": session_key,
            "runningRunId": running_run_id,
            "queued": len(queue),
            "queueDepth": len(queue),
            "headWaitMs": head_wait_ms,
            "oldestEnqueuedAt": queue[0].get("enqueuedAt") if queue else None,
        }
    # Global summary
    lanes_list = lane_list_all(app_state, now_ms)
    running_sessions = sum(1 for s in session_to_run if session_to_run.get(s))
    queued_sessions = len(pending)
    total_queued = sum(len(q) for q in pending.values())
    return {
        "summary": {
            "runningSessions": running_sessions,
            "queuedSessions": queued_sessions,
            "totalQueued": total_queued,
        },
        "lanes": lanes_list,
    }


def lane_list_all(app_state: dict[str, Any], now_ms: int) -> list[dict[str, Any]]:
    """All lanes with running + pending info for lanes.list and HTTP queue API."""
    session_to_run = _get_session_to_run(app_state)
    pending = _get_pending(app_state)
    session_keys = set(session_to_run.keys()) | set(pending.keys())
    out = []
    for sk in sorted(session_keys):
        running_run_id = session_to_run.get(sk)
        queue = pending.get(sk, [])
        head_wait_ms: int | None = None
        if queue and now_ms > 0:
            enq = queue[0].get("enqueuedAt")
            if enq is not None:
                head_wait_ms = now_ms - int(enq)
        out.append({
            "sessionKey": sk,
            "runningRunId": running_run_id,
            "queued": len(queue),
            "queueDepth": len(queue),
            "headWaitMs": head_wait_ms,
            "oldestEnqueuedAt": queue[0].get("enqueuedAt") if queue else None,
        })
    return out
