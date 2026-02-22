"""Shared control overview/channel service helpers."""

from __future__ import annotations

import time
from typing import Any, Callable


def build_channels_status_snapshot(
    *,
    config: Any,
    channel_manager: Any,
    now_ms: Callable[[], int],
) -> dict[str, Any]:
    channel_names = ["telegram", "whatsapp", "feishu", "dingtalk", "discord", "email", "slack", "qq", "mochat"]
    labels = {name: name.title() for name in channel_names}
    status = channel_manager.get_status() if channel_manager else {}
    channels = {}
    for name in channel_names:
        cfg = getattr(config.channels, name, None)
        if cfg is None:
            continue
        channels[name] = {
            "configured": bool(getattr(cfg, "enabled", False)),
            "running": bool(status.get(name, {}).get("running", False)),
            "connected": bool(status.get(name, {}).get("running", False)),
        }
    return {
        "ts": now_ms(),
        "channelOrder": channel_names,
        "channelLabels": labels,
        "channels": channels,
        "channelAccounts": {name: [] for name in channel_names},
        "channelDefaultAccountId": {},
    }


def build_control_channels_payload(*, config: Any, channel_manager: Any, now_ms: Callable[[], int]) -> dict[str, Any]:
    snapshot = build_channels_status_snapshot(config=config, channel_manager=channel_manager, now_ms=now_ms)
    channels = snapshot.get("channels", {}) if isinstance(snapshot, dict) else {}
    rows = [
        {"name": name, "enabled": bool(meta.get("configured")), "running": bool(meta.get("running"))}
        for name, meta in channels.items()
    ]
    return {"ok": True, "channels": rows}


def build_control_overview_payload(
    *,
    agent: Any,
    config: Any,
    channel_manager: Any,
    cron_service: Any,
    start_time: float | None,
    presence_count: int,
    now_ms: Callable[[], int],
    load_control_plane_worker_status: Callable[[], dict[str, Any]],
    build_auth_profiles_report: Callable[[Any], dict[str, Any]],
    build_operational_alerts: Callable[..., list[dict[str, Any]]],
    normalize_operational_alerts: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    apply_alerts_lifecycle: Callable[[list[dict[str, Any]]], tuple[list[dict[str, Any]], dict[str, Any]]],
    build_alerts_summary: Callable[[list[dict[str, Any]]], dict[str, Any]],
    build_actions_catalog: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    uptime_seconds = None
    if start_time is not None:
        uptime_seconds = max(0, int(time.time() - start_time))

    sessions_count = 0
    if agent and hasattr(agent, "sessions"):
        try:
            sessions_count = len(agent.sessions.list_sessions())
        except Exception:
            pass

    cron_status = None
    if cron_service is not None:
        try:
            st = cron_service.status()
            cron_status = {
                "enabled": st.get("enabled", False),
                "jobs": st.get("jobs", 0),
                "next_wake_at_ms": st.get("next_wake_at_ms"),
            }
        except Exception:
            cron_status = {"enabled": False, "jobs": 0, "next_wake_at_ms": None}

    channels_snapshot = build_channels_status_snapshot(config=config, channel_manager=channel_manager, now_ms=now_ms)
    channels_items = channels_snapshot.get("channels", {}) if isinstance(channels_snapshot, dict) else {}
    channels_summary = {
        "count": len(channels_items),
        "configured": sum(1 for s in channels_items.values() if bool(s.get("configured"))),
        "running": sum(1 for s in channels_items.values() if bool(s.get("running"))),
        "channels": channels_items,
    }

    gateway_host = getattr(config.gateway, "host", "127.0.0.1")
    gateway_port = getattr(config.gateway, "port", 18790)

    auth_profiles = build_auth_profiles_report(config)
    control_plane_status = load_control_plane_worker_status()
    alerts = build_operational_alerts(
        auth_profiles=auth_profiles,
        channels_snapshot=channels_snapshot,
        cron_status=cron_status,
        control_plane_status=control_plane_status,
    )
    alerts = normalize_operational_alerts(alerts)
    alerts, alerts_lifecycle = apply_alerts_lifecycle(alerts)
    alerts_summary = build_alerts_summary(alerts)
    alerts_summary["resolvedRecentCount"] = int(alerts_lifecycle.get("resolvedRecentCount") or 0)
    health_ok = int(alerts_summary.get("critical") or 0) == 0

    return {
        "ok": True,
        "health": health_ok,
        "uptime_seconds": uptime_seconds,
        "gateway": {"host": gateway_host, "port": gateway_port},
        "sessions_count": sessions_count,
        "presence_count": presence_count,
        "cron": cron_status,
        "channels": channels_summary,
        "channelsSnapshot": channels_snapshot,
        "controlPlane": control_plane_status,
        "authProfiles": auth_profiles,
        "alerts": alerts,
        "alertsSummary": alerts_summary,
        "alertsLifecycle": alerts_lifecycle,
        "actionsCatalog": build_actions_catalog(),
    }

