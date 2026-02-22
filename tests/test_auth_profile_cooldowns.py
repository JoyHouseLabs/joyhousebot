from types import SimpleNamespace

from joyhousebot.agent.auth_profiles import (
    build_auth_profile_alerts,
    build_auth_profiles_report,
    classify_failover_reason,
    is_profile_available,
    mark_profile_failure,
    mark_profile_success,
    resolve_profile_order,
    resolve_unusable_until,
)


def _config_stub():
    return SimpleNamespace(
        auth=SimpleNamespace(
            profiles={
                "p1": SimpleNamespace(provider="openai", enabled=True),
                "p2": SimpleNamespace(provider="openai", enabled=True),
                "p3": SimpleNamespace(provider="anthropic", enabled=True),
                "p4": SimpleNamespace(provider="openai", enabled=False),
            },
            order={"openai": ["p2", "p1", "missing"]},
            cooldowns=SimpleNamespace(
                billing_backoff_hours=5.0,
                billing_backoff_hours_by_provider={"openai": 2.0},
                billing_max_hours=24.0,
                failure_window_hours=24.0,
            ),
        )
    )


def test_resolve_profile_order_prefers_explicit_then_rest() -> None:
    cfg = _config_stub()
    assert resolve_profile_order(cfg, "openai") == ["p2", "p1"]
    assert resolve_profile_order(cfg, "anthropic") == ["p3"]


def test_mark_profile_failure_regular_and_billing_backoff() -> None:
    cfg = _config_stub()
    usage: dict[str, dict] = {}
    now_ms = 1_000_000.0

    mark_profile_failure(usage, profile_id="p1", provider="openai", reason="rate_limit", config=cfg, now_ms=now_ms)
    stats = usage["p1"]
    assert stats["failure_count"] == 1
    assert stats["cooldown_until_ms"] > now_ms
    assert is_profile_available(usage, "p1", now_ms=now_ms) is False

    mark_profile_failure(usage, profile_id="p2", provider="openai", reason="billing", config=cfg, now_ms=now_ms)
    billing_stats = usage["p2"]
    # provider override base_h=2.0, first failure = 2h
    assert billing_stats["disabled_until_ms"] == now_ms + 2.0 * 3600_000
    assert resolve_unusable_until(billing_stats) == billing_stats["disabled_until_ms"]


def test_mark_profile_success_clears_cooldown() -> None:
    usage = {"p1": {"failure_count": 3, "cooldown_until_ms": 9999999999, "disabled_until_ms": 9999999999}}
    mark_profile_success(usage, "p1", now_ms=123456.0)
    stats = usage["p1"]
    assert stats["failure_count"] == 0
    assert stats["cooldown_until_ms"] == 0
    assert stats["disabled_until_ms"] == 0
    assert is_profile_available(usage, "p1", now_ms=123456.0) is True


def test_classify_failover_reason() -> None:
    assert classify_failover_reason("429 Too many requests") == "rate_limit"
    assert classify_failover_reason("insufficient credits") == "billing"
    assert classify_failover_reason("401 unauthorized") == "auth"
    assert classify_failover_reason("request timed out") == "timeout"
    assert classify_failover_reason("unknown error") == "unknown"


def test_build_auth_profiles_report(monkeypatch) -> None:
    cfg = _config_stub()
    fake_usage = {
        "p1": {"failure_count": 2, "cooldown_until_ms": 2000, "last_failure_ms": 1000},
        "p2": {"failure_count": 0, "cooldown_until_ms": 0, "last_used_ms": 1500},
    }
    monkeypatch.setattr("joyhousebot.agent.auth_profiles.load_profile_usage", lambda: fake_usage)
    report = build_auth_profiles_report(cfg, now_ms=1500)
    assert report["status"] == "degraded"
    assert report["totalProfiles"] == 4
    assert report["availableProfiles"] == 2
    assert report["unavailableProfiles"] == 2
    assert report["providers"][0]["provider"] == "anthropic"
    openai = next(x for x in report["providers"] if x["provider"] == "openai")
    assert openai["total"] == 3
    assert openai["available"] == 1
    assert openai["unavailable"] == 2
    assert openai["disabled"] == 0
    assert openai["status"] == "degraded"
    p1 = next(x for x in report["profiles"] if x["profileId"] == "p1")
    assert p1["available"] is False
    assert p1["state"] == "cooldown"
    assert p1["failureCount"] == 2
    p4 = next(x for x in report["profiles"] if x["profileId"] == "p4")
    assert p4["state"] == "disabled"


def test_build_auth_profile_alerts() -> None:
    report = {
        "status": "degraded",
        "providers": [
            {"provider": "openai", "status": "down", "nextRecoveryMs": 123},
            {"provider": "anthropic", "status": "ok", "nextRecoveryMs": None},
        ],
    }
    alerts = build_auth_profile_alerts(report)
    codes = {a["code"] for a in alerts}
    assert "AUTH_PROFILES_DEGRADED" in codes
    assert "AUTH_PROVIDER_DOWN" in codes
    provider_down = next(a for a in alerts if a["code"] == "AUTH_PROVIDER_DOWN")
    assert provider_down["source"] == "auth"
    assert provider_down["level"] == "critical"

