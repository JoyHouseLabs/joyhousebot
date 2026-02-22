from joyhousebot.config.schema import Config
from joyhousebot.services.control.overview_service import (
    build_channels_status_snapshot,
    build_control_channels_payload,
    build_control_overview_payload,
)


def test_build_channels_snapshot_and_channels_payload():
    cfg = Config()
    cfg.channels.telegram.enabled = True
    snapshot = build_channels_status_snapshot(config=cfg, channel_manager=None, now_ms=lambda: 123)
    assert snapshot["ts"] == 123
    assert snapshot["channels"]["telegram"]["configured"] is True

    payload = build_control_channels_payload(config=cfg, channel_manager=None, now_ms=lambda: 1)
    assert payload["ok"] is True
    assert any(row["name"] == "telegram" for row in payload["channels"])


def test_build_control_overview_payload_minimal():
    cfg = Config()
    cfg.channels.telegram.enabled = True
    payload = build_control_overview_payload(
        agent=None,
        config=cfg,
        channel_manager=None,
        cron_service=None,
        start_time=None,
        presence_count=0,
        now_ms=lambda: 1,
        load_control_plane_worker_status=lambda: {},
        build_auth_profiles_report=lambda _cfg: {"status": "ok"},
        build_operational_alerts=lambda **_: [],
        normalize_operational_alerts=lambda alerts: alerts,
        apply_alerts_lifecycle=lambda alerts: (alerts, {"resolvedRecentCount": 0}),
        build_alerts_summary=lambda alerts: {"critical": 0, "warning": 0, "total": len(alerts)},
        build_actions_catalog=lambda: {"actions": [], "count": 0},
    )
    assert payload["ok"] is True
    assert payload["health"] is True
    assert "channelsSnapshot" in payload

