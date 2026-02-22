"""Helpers for control-related HTTP endpoints."""

from __future__ import annotations

from typing import Any, Callable

from joyhousebot.services.control.overview_service import (
    build_control_channels_payload,
    build_control_overview_payload,
)


def control_overview_response(
    *,
    app_state: dict[str, Any],
    config: Any,
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
    return build_control_overview_payload(
        agent=app_state.get("agent_loop"),
        config=config,
        channel_manager=app_state.get("channel_manager"),
        cron_service=app_state.get("cron_service"),
        start_time=app_state.get("_start_time"),
        presence_count=presence_count,
        now_ms=now_ms,
        load_control_plane_worker_status=load_control_plane_worker_status,
        build_auth_profiles_report=build_auth_profiles_report,
        build_operational_alerts=build_operational_alerts,
        normalize_operational_alerts=normalize_operational_alerts,
        apply_alerts_lifecycle=apply_alerts_lifecycle,
        build_alerts_summary=build_alerts_summary,
        build_actions_catalog=build_actions_catalog,
    )


def control_channels_response(
    *,
    config: Any,
    channel_manager: Any,
    now_ms: Callable[[], int],
) -> dict[str, Any]:
    return build_control_channels_payload(config=config, channel_manager=channel_manager, now_ms=now_ms)


def control_queue_response(
    *,
    app_state: dict[str, Any],
    now_ms: Callable[[], int],
) -> dict[str, Any]:
    """Queue metrics for control UI: lanes (sessionKey, runningRunId, queued, queueDepth, headWaitMs)."""
    from joyhousebot.services.lanes import lane_list_all, lane_status

    lanes_list = lane_list_all(app_state, now_ms())
    status_full = lane_status(app_state, None, now_ms())
    summary = status_full.get("summary", {})
    return {
        "ok": True,
        "sessions": lanes_list,
        "summary": summary,
        "ts": now_ms(),
    }

