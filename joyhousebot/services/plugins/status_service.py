"""Shared helpers for plugin status payload shaping."""

from __future__ import annotations

import time
from typing import Any


def build_compact_status_report(report: dict[str, Any]) -> dict[str, Any]:
    runtime = report.get("nativeRuntime", {}) if isinstance(report, dict) else {}
    totals = runtime.get("totals", {}) if isinstance(runtime, dict) else {}
    last_24h = runtime.get("last24h", {}) if isinstance(runtime, dict) else {}
    plugins_row = report.get("plugins", {}) if isinstance(report, dict) else {}
    return {
        "ok": bool(report.get("ok", False)),
        "tsMs": int(report.get("tsMs", 0) or 0),
        "pluginsTotal": int(plugins_row.get("total", 0) or 0),
        "pluginsLoaded": int(plugins_row.get("loaded", 0) or 0),
        "pluginsErrored": int(plugins_row.get("errored", 0) or 0),
        "nativeCalls": int(totals.get("calls", 0) or 0),
        "nativeErrors": int(totals.get("errors", 0) or 0),
        "nativeTimeouts": int(totals.get("timeouts", 0) or 0),
        "nativeCircuitOpenHits24h": int(last_24h.get("circuitOpenHits", 0) or 0),
        "nativeCircuitHitRate24h": float(last_24h.get("circuitHitRate", 0.0) or 0.0),
    }


def build_status_metric_rows(report: dict[str, Any]) -> list[tuple[str, str]]:
    plugins_row = report.get("plugins", {}) if isinstance(report, dict) else {}
    return [
        ("plugins.total", str(plugins_row.get("total", 0))),
        ("plugins.loaded", str(plugins_row.get("loaded", 0))),
        ("plugins.errored", str(plugins_row.get("errored", 0))),
        ("gateway.methods", str(report.get("gatewayMethods", 0))),
        ("tools", str(report.get("tools", 0))),
        ("services", str(report.get("services", 0))),
        ("channels", str(report.get("channels", 0))),
        ("providers", str(report.get("providers", 0))),
        ("hooks", str(report.get("hooks", 0))),
        ("skills.dirs", str(report.get("skillsDirs", 0))),
        ("ts", str(report.get("tsMs", int(time.time() * 1000)))),
    ]

