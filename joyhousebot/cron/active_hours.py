"""Validation and evaluation for Monitor active-hour windows."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def normalize_active_hours(value: Any) -> dict[str, str] | None:
    """Return a canonical active-hours value or raise for invalid policy."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("active_hours must be an object")
    start = str(value.get("start") or "").strip()
    end = str(value.get("end") or "").strip()
    timezone = str(value.get("timezone") or "").strip()
    if not _CLOCK.fullmatch(start) or not _CLOCK.fullmatch(end):
        raise ValueError("active_hours start/end must use HH:MM")
    if not timezone:
        raise ValueError("active_hours timezone is required")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("active_hours timezone is invalid") from exc
    return {"start": start, "end": end, "timezone": timezone}


def is_within_active_hours(value: dict[str, str] | None, now_ms: int) -> bool:
    """Evaluate a local-time window, including windows crossing midnight."""
    hours = normalize_active_hours(value)
    if hours is None:
        return True
    local = datetime.fromtimestamp(now_ms / 1000, tz=ZoneInfo(hours["timezone"]))
    minute = local.hour * 60 + local.minute
    start_hour, start_minute = (int(item) for item in hours["start"].split(":"))
    end_hour, end_minute = (int(item) for item in hours["end"].split(":"))
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute
    if start == end:
        return True
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end
