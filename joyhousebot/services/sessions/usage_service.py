"""Shared session usage aggregation operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable


def build_usage_payload(
    *,
    params: dict[str, Any],
    now_ms: Callable[[], int],
    agent: Any,
    empty_usage_totals: Callable[[], dict[str, Any]],
    session_usage_entry: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
) -> dict[str, Any]:
    start_date = str(params.get("startDate") or "")
    end_date = str(params.get("endDate") or "")
    sessions = []
    totals = empty_usage_totals()
    total_messages = {"total": 0, "user": 0, "assistant": 0, "toolCalls": 0, "toolResults": 0, "errors": 0}
    for meta in agent.sessions.list_sessions()[:1000]:
        key = str(meta.get("key") or "")
        session = agent.sessions.get_or_create(key)
        entry = session_usage_entry(key, session.messages)
        sessions.append(entry)
        usage = entry["usage"]
        totals["input"] += usage["input"]
        totals["output"] += usage["output"]
        totals["totalTokens"] += usage["totalTokens"]
        counts = usage.get("messageCounts") or {}
        for k in total_messages:
            total_messages[k] += int(counts.get(k, 0))
    return {
        "updatedAt": now_ms(),
        "startDate": start_date,
        "endDate": end_date,
        "sessions": sessions,
        "totals": totals,
        "aggregates": {
            "messages": total_messages,
            "tools": {"totalCalls": total_messages["toolCalls"], "uniqueTools": 0, "tools": []},
            "byModel": [],
            "byProvider": [],
            "byAgent": [],
            "byChannel": [],
            "daily": [],
        },
    }


def build_usage_timeseries(
    *,
    key: str,
    agent: Any,
    now_ms: Callable[[], int],
    estimate_tokens: Callable[[str], int],
) -> dict[str, Any]:
    session = agent.sessions.get_or_create(key)
    points = []
    cumulative_tokens = 0
    for message in session.messages:
        ts_raw = message.get("timestamp")
        ts = now_ms()
        if isinstance(ts_raw, str):
            try:
                ts = int(datetime.fromisoformat(ts_raw).timestamp() * 1000)
            except Exception:
                pass
        tokens = estimate_tokens(str(message.get("content") or ""))
        cumulative_tokens += tokens
        points.append(
            {
                "timestamp": ts,
                "input": tokens if message.get("role") == "user" else 0,
                "output": tokens if message.get("role") == "assistant" else 0,
                "cacheRead": 0,
                "cacheWrite": 0,
                "totalTokens": tokens,
                "cost": 0,
                "cumulativeTokens": cumulative_tokens,
                "cumulativeCost": 0,
            }
        )
    return {"sessionId": key, "points": points}


def build_usage_logs(
    *,
    key: str,
    limit: int,
    agent: Any,
    estimate_tokens: Callable[[str], int],
) -> dict[str, Any]:
    session = agent.sessions.get_or_create(key)
    logs = []
    for message in session.messages[-limit:]:
        logs.append(
            {
                "timestamp": message.get("timestamp"),
                "role": message.get("role"),
                "text": str(message.get("content") or "")[:1000],
                "tokens": estimate_tokens(str(message.get("content") or "")),
                "cost": 0,
            }
        )
    return {"logs": logs}

