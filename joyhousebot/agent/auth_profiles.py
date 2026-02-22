"""Auth profile selection and cooldown utilities."""

from __future__ import annotations

import time
from typing import Any

from joyhousebot.storage import LocalStateStore

_USAGE_SYNC_KEY = "auth.profile_usage"


def load_profile_usage() -> dict[str, dict[str, Any]]:
    store = LocalStateStore.default()
    raw = store.get_sync_json(name=_USAGE_SYNC_KEY, default={})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, dict):
            out[key] = dict(value)
    return out


def save_profile_usage(usage: dict[str, dict[str, Any]]) -> None:
    store = LocalStateStore.default()
    store.set_sync_json(name=_USAGE_SYNC_KEY, value=usage)


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
    for pid, p in (profiles.items() if isinstance(profiles, dict) else []):
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


def is_profile_available(usage: dict[str, dict[str, Any]], profile_id: str, now_ms: float | None = None) -> bool:
    now = float(now_ms or (time.time() * 1000))
    stats = usage.get(profile_id, {})
    return resolve_unusable_until(stats) <= now


def mark_profile_success(usage: dict[str, dict[str, Any]], profile_id: str, now_ms: float | None = None) -> None:
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


def build_auth_profiles_report(config: Any, now_ms: float | None = None) -> dict[str, Any]:
    now = float(now_ms or (time.time() * 1000))
    usage = load_profile_usage()
    profiles = getattr(getattr(config, "auth", None), "profiles", {}) or {}
    by_provider: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    total_profiles = 0
    available_profiles = 0
    unavailable_profiles = 0
    for profile_id, profile in (profiles.items() if isinstance(profiles, dict) else []):
        total_profiles += 1
        provider = str(getattr(profile, "provider", "")).strip() or "unknown"
        enabled = bool(getattr(profile, "enabled", True))
        stats = usage.get(profile_id, {})
        unusable_until = resolve_unusable_until(stats)
        available = unusable_until <= now and enabled
        in_cooldown = bool(stats.get("cooldown_until_ms") or 0) and float(stats.get("cooldown_until_ms") or 0) > now
        disabled = bool(stats.get("disabled_until_ms") or 0) and float(stats.get("disabled_until_ms") or 0) > now
        state = "available" if available else ("disabled" if (disabled or not enabled) else "cooldown")
        row = {
            "profileId": profile_id,
            "provider": provider,
            "mode": str(getattr(profile, "mode", "api_key")),
            "enabled": enabled,
            "available": available,
            "state": state,
            "failureCount": int(stats.get("failure_count") or 0),
            "lastFailureMs": stats.get("last_failure_ms"),
            "lastUsedMs": stats.get("last_used_ms"),
            "cooldownUntilMs": stats.get("cooldown_until_ms"),
            "disabledUntilMs": stats.get("disabled_until_ms"),
            "unusableUntilMs": int(unusable_until) if unusable_until > 0 else None,
        }
        rows.append(row)
        summary = by_provider.setdefault(
            provider,
            {
                "provider": provider,
                "total": 0,
                "available": 0,
                "unavailable": 0,
                "cooldown": 0,
                "disabled": 0,
                "status": "ok",
                "nextRecoveryMs": None,
            },
        )
        summary["total"] += 1
        if available:
            available_profiles += 1
            summary["available"] += 1
        else:
            unavailable_profiles += 1
            summary["unavailable"] += 1
            if in_cooldown:
                summary["cooldown"] += 1
            if disabled:
                summary["disabled"] += 1
            next_recovery = row.get("unusableUntilMs")
            if isinstance(next_recovery, int):
                current = summary.get("nextRecoveryMs")
                summary["nextRecoveryMs"] = next_recovery if not isinstance(current, int) else min(current, next_recovery)
    rows.sort(key=lambda r: (r["provider"], r["profileId"]))
    providers = sorted(by_provider.values(), key=lambda x: x["provider"])
    for summary in providers:
        if int(summary.get("available") or 0) == 0 and int(summary.get("total") or 0) > 0:
            summary["status"] = "down"
        elif int(summary.get("unavailable") or 0) > 0:
            summary["status"] = "degraded"
        else:
            summary["status"] = "ok"
    if total_profiles == 0:
        report_status = "empty"
    elif available_profiles == 0:
        report_status = "down"
    elif unavailable_profiles > 0:
        report_status = "degraded"
    else:
        report_status = "ok"
    return {
        "status": report_status,
        "totalProfiles": total_profiles,
        "availableProfiles": available_profiles,
        "unavailableProfiles": unavailable_profiles,
        "providers": providers,
        "profiles": rows,
        "ts": int(now),
    }


def build_auth_profile_alerts(report: dict[str, Any]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    overall = str(report.get("status") or "")
    if overall == "down":
        alerts.append(
            {
                "source": "auth",
                "category": "availability",
                "level": "critical",
                "code": "AUTH_PROFILES_DOWN",
                "severity": "critical",
                "title": "All auth profiles unavailable",
                "message": "No available auth profile. Model calls will fail until recovery.",
            }
        )
    elif overall == "degraded":
        alerts.append(
            {
                "source": "auth",
                "category": "availability",
                "level": "warning",
                "code": "AUTH_PROFILES_DEGRADED",
                "severity": "warning",
                "title": "Auth profiles partially unavailable",
                "message": "Some auth profiles are in cooldown/disabled state.",
            }
        )
    for provider in report.get("providers") or []:
        if not isinstance(provider, dict):
            continue
        provider_name = str(provider.get("provider") or "unknown")
        status = str(provider.get("status") or "")
        next_recovery = provider.get("nextRecoveryMs")
        if status == "down":
            alerts.append(
                {
                    "source": "auth",
                    "category": "provider",
                    "level": "critical",
                    "code": "AUTH_PROVIDER_DOWN",
                    "severity": "critical",
                    "provider": provider_name,
                    "title": f"{provider_name} profiles unavailable",
                    "message": f"All {provider_name} profiles are unavailable.",
                    "nextRecoveryMs": next_recovery,
                }
            )
        elif status == "degraded":
            alerts.append(
                {
                    "source": "auth",
                    "category": "provider",
                    "level": "warning",
                    "code": "AUTH_PROVIDER_DEGRADED",
                    "severity": "warning",
                    "provider": provider_name,
                    "title": f"{provider_name} profiles degraded",
                    "message": f"Some {provider_name} profiles are unavailable.",
                    "nextRecoveryMs": next_recovery,
                }
            )
    return alerts

