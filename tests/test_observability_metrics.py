from porthouse.observability.otel import configure_telemetry, telemetry_span
from porthouse.observability.prometheus import render_prometheus


def test_prometheus_renderer_covers_governed_runtime_families() -> None:
    text = render_prometheus(
        {
            "runs": {"completed": 3},
            "tasks": {"queued": 2},
            "workers": {"online": 1},
            "runs_24h": [{"status": "failed", "count": 1, "p95_duration_ms": 25}],
            "queue": {"queued": 2, "claim_delay_p95_ms": 12.5},
            "actions": {"completed": 4},
            "approvals": {"pending": 1},
            "reconciliations": {"manual_required": 1},
            "sagas": {"completed": 1},
            "graph_patch_proposals": {"approved": 1},
            "eval_runs": {"passed": 2},
            "work_shares": {"active": 2},
            "app_callback_deliveries": {"dead": 1},
            "app_callback_oldest_pending_seconds": 15,
            "verifications": [
                {"status": "passed", "verifier_type": "schema", "count": 3}
            ],
            "works": [{"status": "published", "visibility": "public", "count": 1}],
            "api_tokens": [{"token_type": "service", "status": "active", "count": 2}],
            "api_token_risks": {"rotation_overdue": 1},
            "critical_events_24h": {"lease.lost": 1},
            "active_ages": {"saga": 9},
        }
    )
    assert 'porthouse_runs_total{status="completed"} 3' in text
    assert "porthouse_task_claim_delay_ms_p95 12.5" in text
    assert 'porthouse_reconciliations_total{status="manual_required"} 1' in text
    assert 'porthouse_verifications_total{status="passed",verifier_type="schema"} 3' in text
    assert 'porthouse_oldest_active_seconds{kind="saga"} 9.0' in text
    assert 'event_type="lease.lost"' in text
    assert 'porthouse_api_tokens_total{token_type="service",status="active"} 2' in text
    assert 'porthouse_api_token_risks_total{risk="rotation_overdue"} 1' in text
    assert 'porthouse_app_callback_deliveries_total{status="dead"} 1' in text
    assert "porthouse_app_callback_oldest_pending_seconds 15.0" in text


def test_telemetry_is_inert_until_explicitly_enabled(monkeypatch) -> None:
    monkeypatch.delenv("PORTHOUSE_OTEL_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert configure_telemetry(service_name="test-disabled") is False
    with telemetry_span("test.disabled") as span:
        assert span is None
