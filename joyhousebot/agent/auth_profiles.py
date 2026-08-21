"""Auth profile selection and cooldown transitions."""

from __future__ import annotations

import time
from typing import Any


def classify_failover_reason(text: str) -> str:
    msg = (text or "").lower()
    if any(x in msg for x in ("insufficient", "credit", "billing", "payment", "quota exceeded")):
        return "billing"
    if any(x in msg for x in ("rate limit", "too many requests", "429")):
        return "rate_limit"
    if any(x in msg for x in ("unauthorized", "invalid api key", "forbidden", "401", "403")):
        return "auth"
    if any(x in msg for x in ("timeout", "timed out", "deadline exceeded")):
        return "timeout"
    return "unknown"


def resolve_profile_order(config: Any, provider: str) -> list[str]:
    auth = getattr(config, "auth", None)
    profiles = getattr(auth, "profiles", {}) if auth else {}
    order = getattr(auth, "order", {}) if auth else {}

    explicit = order.get(provider, []) if isinstance(order, dict) else []
    out: list[str] = []
    seen: set[str] = set()
    for pid in explicit:
        if not isinstance(pid, str) or not pid.strip() or pid in seen:
            continue
        p = profiles.get(pid)
        if p is None or not getattr(p, "enabled", True):
            continue
        if str(getattr(p, "provider", "")).strip() != provider:
            continue
        seen.add(pid)
        out.append(pid)
    for pid, p in profiles.items() if isinstance(profiles, dict) else []:
        if pid in seen:
            continue
        if not getattr(p, "enabled", True):
            continue
        if str(getattr(p, "provider", "")).strip() != provider:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def resolve_unusable_until(stats: dict[str, Any]) -> float:
    cooldown = float(stats.get("cooldown_until_ms") or 0)
    disabled = float(stats.get("disabled_until_ms") or 0)
    return max(cooldown, disabled)


def is_profile_available(
    usage: dict[str, dict[str, Any]], profile_id: str, now_ms: float | None = None
) -> bool:
    now = float(now_ms or (time.time() * 1000))
    stats = usage.get(profile_id, {})
    return resolve_unusable_until(stats) <= now


def mark_profile_success(
    usage: dict[str, dict[str, Any]], profile_id: str, now_ms: float | None = None
) -> None:
    now = float(now_ms or (time.time() * 1000))
    stats = dict(usage.get(profile_id, {}))
    stats["last_used_ms"] = now
    stats["failure_count"] = 0
    stats["cooldown_until_ms"] = 0
    stats["disabled_until_ms"] = 0
    usage[profile_id] = stats


def mark_profile_failure(
    usage: dict[str, dict[str, Any]],
    *,
    profile_id: str,
    provider: str,
    reason: str,
    config: Any,
    now_ms: float | None = None,
) -> None:
    now = float(now_ms or (time.time() * 1000))
    stats = dict(usage.get(profile_id, {}))
    cooldowns = getattr(getattr(config, "auth", None), "cooldowns", None)
    failure_window_h = float(getattr(cooldowns, "failure_window_hours", 24.0) or 24.0)
    last_failure = float(stats.get("last_failure_ms") or 0)
    if now - last_failure > failure_window_h * 3600_000:
        failure_count = 0
    else:
        failure_count = int(stats.get("failure_count") or 0)
    failure_count += 1
    stats["failure_count"] = failure_count
    stats["last_failure_ms"] = now

    if reason == "billing":
        base_h = float(getattr(cooldowns, "billing_backoff_hours", 5.0) or 5.0)
        by_provider = getattr(cooldowns, "billing_backoff_hours_by_provider", {}) or {}
        if isinstance(by_provider, dict) and provider in by_provider:
            base_h = float(by_provider.get(provider) or base_h)
        max_h = float(getattr(cooldowns, "billing_max_hours", 24.0) or 24.0)
        hours = min(max_h, base_h * (2 ** max(0, failure_count - 1)))
        stats["disabled_until_ms"] = now + hours * 3600_000
    else:
        # Short exponential cooldown for transient/provider failures.
        cooldown_s = min(1800.0, 15.0 * (2 ** max(0, failure_count - 1)))
        stats["cooldown_until_ms"] = now + cooldown_s * 1000
    usage[profile_id] = stats
