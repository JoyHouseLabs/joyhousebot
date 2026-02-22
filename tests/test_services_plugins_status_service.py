from joyhousebot.services.plugins.status_service import build_compact_status_report, build_status_metric_rows


def test_build_compact_status_report():
    report = {
        "ok": True,
        "tsMs": 123,
        "plugins": {"total": 3, "loaded": 2, "errored": 1},
        "nativeRuntime": {
            "totals": {"calls": 10, "errors": 2, "timeouts": 1},
            "last24h": {"circuitOpenHits": 4, "circuitHitRate": 0.25},
        },
    }
    compact = build_compact_status_report(report)
    assert compact["ok"] is True
    assert compact["pluginsTotal"] == 3
    assert compact["nativeErrors"] == 2
    assert compact["nativeCircuitHitRate24h"] == 0.25


def test_build_status_metric_rows():
    report = {
        "plugins": {"total": 3, "loaded": 2, "errored": 1},
        "gatewayMethods": 6,
        "tools": 7,
        "services": 8,
        "channels": 9,
        "providers": 10,
        "hooks": 11,
        "skillsDirs": 12,
        "tsMs": 123,
    }
    rows = build_status_metric_rows(report)
    row_map = {k: v for k, v in rows}
    assert row_map["plugins.total"] == "3"
    assert row_map["gateway.methods"] == "6"
    assert row_map["ts"] == "123"

