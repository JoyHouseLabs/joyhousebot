from types import SimpleNamespace

from porthouse.agent.auth_profiles import (
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

    mark_profile_failure(
        usage, profile_id="p1", provider="openai", reason="rate_limit", config=cfg, now_ms=now_ms
    )
    stats = usage["p1"]
    assert stats["failure_count"] == 1
    assert stats["cooldown_until_ms"] > now_ms
    assert is_profile_available(usage, "p1", now_ms=now_ms) is False

    mark_profile_failure(
        usage, profile_id="p2", provider="openai", reason="billing", config=cfg, now_ms=now_ms
    )
    billing_stats = usage["p2"]
    # provider override base_h=2.0, first failure = 2h
    assert billing_stats["disabled_until_ms"] == now_ms + 2.0 * 3600_000
    assert resolve_unusable_until(billing_stats) == billing_stats["disabled_until_ms"]


def test_mark_profile_success_clears_cooldown() -> None:
    usage = {
        "p1": {"failure_count": 3, "cooldown_until_ms": 9999999999, "disabled_until_ms": 9999999999}
    }
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
