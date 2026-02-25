"""Shared session usage aggregation operations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


def _parse_date_to_ms(raw: Any) -> int | None:
    """Parse YYYY-MM-DD to start of day UTC timestamp. Return None if invalid."""
    if not raw or not isinstance(raw, str) or not raw.strip():
        return None
    raw = raw.strip()
    if len(raw) < 10:
        return None
    try:
        # YYYY-MM-DD
        year = int(raw[:4])
        month = int(raw[5:7])
        day = int(raw[8:10])
        return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)
    except (ValueError, IndexError):
        return None


def _parse_days(raw: Any) -> int | None:
    """Parse days parameter to integer."""
    if isinstance(raw, int) and raw >= 0:
        return int(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            return int(float(raw.strip()))
        except ValueError:
            pass
    return None


def parse_date_range(params: dict[str, Any]) -> tuple[int, int, str, str]:
    """
    Get date range from params (startDate/endDate or days).
    Aligns with OpenClaw: default last 30 days.
    Returns (start_ms, end_ms, start_date_str, end_date_str).
    """
    now = datetime.now(timezone.utc)
    today_start_ms = int(
        datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp() * 1000
    )
    today_end_ms = today_start_ms + 24 * 60 * 60 * 1000 - 1

    start_ms = _parse_date_to_ms(params.get("startDate"))
    end_ms = _parse_date_to_ms(params.get("endDate"))
    if start_ms is not None and end_ms is not None:
        end_ms = end_ms + 24 * 60 * 60 * 1000 - 1  # end of day
        start_str = params.get("startDate", "")[:10] if params.get("startDate") else ""
        end_str = params.get("endDate", "")[:10] if params.get("endDate") else ""
        return start_ms, end_ms, start_str, end_str

    days = _parse_days(params.get("days"))
    if days is not None and days >= 1:
        clamped = max(1, days)
        start_ms = today_start_ms - (clamped - 1) * 24 * 60 * 60 * 1000
        end_ms = today_end_ms
        start_str = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        end_str = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        return start_ms, end_ms, start_str, end_str

    # Default: last 30 days
    start_ms = today_start_ms - 29 * 24 * 60 * 60 * 1000
    end_ms = today_end_ms
    start_str = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
    end_str = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
    return start_ms, end_ms, start_str, end_str


def _iso_to_ms(iso: Any) -> int | None:
    """Parse ISO timestamp string to ms. Return None if invalid."""
    if iso is None:
        return None
    if isinstance(iso, (int, float)) and iso > 0:
        return int(iso) if iso >= 1e12 else int(iso * 1000)
    if isinstance(iso, str) and iso.strip():
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            pass
    return None


def build_usage_payload(
    *,
    params: dict[str, Any],
    now_ms: Callable[[], int],
    agent: Any,
    empty_usage_totals: Callable[[], dict[str, Any]],
    session_usage_entry: Callable[[str, list[dict[str, Any]]], dict[str, Any]],
) -> dict[str, Any]:
    start_ms, end_ms, start_date, end_date = parse_date_range(params)
    limit = min(1000, max(1, int(params.get("limit") or 1000)))
    sessions_meta = agent.sessions.list_sessions()

    # Filter sessions by updated_at in [start_ms, end_ms]; include sessions with no timestamp
    in_range: list[dict[str, Any]] = []
    for meta in sessions_meta:
        key = str(meta.get("key") or "")
        if not key:
            continue
        updated_at = meta.get("updated_at")
        u_ms = _iso_to_ms(updated_at)
        if u_ms is None:
            in_range.append(meta)
        elif start_ms <= u_ms <= end_ms:
            in_range.append(meta)
    # Sort by updated_at descending (newest first), then take limit
    in_range.sort(
        key=lambda m: _iso_to_ms(m.get("updated_at")) or 0,
        reverse=True,
    )
    in_range = in_range[:limit]

    sessions = []
    totals = empty_usage_totals()
    total_messages = {
        "total": 0,
        "user": 0,
        "assistant": 0,
        "toolCalls": 0,
        "toolResults": 0,
        "errors": 0,
    }
    # daily: date -> CostUsageDailyEntry (input, output, cacheRead, cacheWrite, totalTokens, totalCost, ...)
    daily_map: dict[str, dict[str, Any]] = {}

    for meta in in_range[:limit]:
        key = str(meta.get("key") or "")
        session = agent.sessions.get_or_create(key)
        entry = session_usage_entry(key, session.messages)
        sessions.append(entry)
        usage = entry.get("usage") or {}
        totals["input"] += int(usage.get("input") or 0)
        totals["output"] += int(usage.get("output") or 0)
        totals["cacheRead"] += int(usage.get("cacheRead") or 0)
        totals["cacheWrite"] += int(usage.get("cacheWrite") or 0)
        totals["totalTokens"] += int(usage.get("totalTokens") or 0)
        totals["totalCost"] += float(usage.get("totalCost") or 0)
        totals["inputCost"] += float(usage.get("inputCost") or 0)
        totals["outputCost"] += float(usage.get("outputCost") or 0)
        totals["cacheReadCost"] += float(usage.get("cacheReadCost") or 0)
        totals["cacheWriteCost"] += float(usage.get("cacheWriteCost") or 0)
        totals["missingCostEntries"] += int(usage.get("missingCostEntries") or 0)
        for k in total_messages:
            total_messages[k] += int((usage.get("messageCounts") or {}).get(k, 0))

        for day_entry in usage.get("dailyBreakdown") or []:
            day = day_entry.get("date") or ""
            if not day:
                continue
            if day not in daily_map:
                daily_map[day] = {
                    "date": day,
                    "input": 0,
                    "output": 0,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "totalTokens": 0,
                    "totalCost": 0,
                    "inputCost": 0,
                    "outputCost": 0,
                    "cacheReadCost": 0,
                    "cacheWriteCost": 0,
                    "messages": 0,
                    "toolCalls": 0,
                    "errors": 0,
                }
            daily_map[day]["totalTokens"] += int(day_entry.get("tokens") or 0)
            daily_map[day]["totalCost"] += float(day_entry.get("cost") or 0)
            daily_map[day]["messages"] += int(day_entry.get("messages") or 0)
            daily_map[day]["toolCalls"] += int(day_entry.get("toolCalls") or 0)
            daily_map[day]["errors"] += int(day_entry.get("errors") or 0)

    daily_list = [daily_map[d] for d in sorted(daily_map)]
    return {
        "updatedAt": now_ms(),
        "startDate": start_date,
        "endDate": end_date,
        "sessions": sessions,
        "totals": totals,
        "aggregates": {
            "messages": total_messages,
            "tools": {
                "totalCalls": total_messages["toolCalls"],
                "uniqueTools": 0,
                "tools": [],
            },
            "byModel": [],
            "byProvider": [],
            "byAgent": [],
            "byChannel": [],
            "daily": daily_list,
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
    cumulative_cost = 0.0
    for message in session.messages:
        ts_raw = message.get("timestamp")
        ts = now_ms()
        if isinstance(ts_raw, str):
            try:
                ts = int(datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp() * 1000)
            except Exception:
                pass
        elif isinstance(ts_raw, (int, float)) and ts_raw > 0:
            ts = int(ts_raw) if ts_raw >= 1e12 else int(ts_raw * 1000)
        msg_usage = message.get("usage")
        if isinstance(msg_usage, dict):
            inp = int(msg_usage.get("input") or msg_usage.get("prompt_tokens") or 0)
            out = int(msg_usage.get("output") or msg_usage.get("completion_tokens") or 0)
            tokens = inp + out
        else:
            tokens = estimate_tokens(str(message.get("content") or ""))
            inp = tokens if message.get("role") == "user" else 0
            out = tokens if message.get("role") == "assistant" else 0
        cost = float(message.get("cost") or 0)
        cumulative_tokens += tokens
        cumulative_cost += cost
        points.append(
            {
                "timestamp": ts,
                "input": inp,
                "output": out,
                "cacheRead": 0,
                "cacheWrite": 0,
                "totalTokens": tokens,
                "cost": cost,
                "cumulativeTokens": cumulative_tokens,
                "cumulativeCost": cumulative_cost,
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
        content = str(message.get("content") or "")
        msg_usage = message.get("usage")
        if isinstance(msg_usage, dict):
            tokens = int(
                msg_usage.get("total_tokens")
                or (msg_usage.get("input") or 0) + (msg_usage.get("output") or 0)
                or 0
            )
        else:
            tokens = estimate_tokens(content)
        logs.append(
            {
                "timestamp": message.get("timestamp"),
                "role": message.get("role", "assistant"),
                "content": content[:1000],
                "tokens": tokens,
                "cost": float(message.get("cost") or 0),
            }
        )
    return {"logs": logs}
