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
            "capacity": {
                "reporting_workers": 2,
                "agent_slots": 8,
                "agent_active": 3,
                "agent_waiting": 1,
                "graph_slots": 8,
                "graph_active": 2,
                "graph_buffered": 1,
                "worker_cpu_percent_avg": 35.5,
                "worker_rss_bytes": 1024,
            },
            "database_pool": {"size": 4, "available": 2, "waiting": 1, "max_size": 6},
            "provider_errors_24h": [
                {"provider": "test", "model": "test/model", "failed": 2, "failure_rate": 10.0}
            ],
            "team_runs": [{"status": "waiting_input", "count": 2}, {"status": "completed", "count": 5}],
            "team_plan_actions": [{"action": "confirmed", "count": 3}, {"action": "regenerate_requested", "count": 1}],
            "team_planning": {
                "planning_duration_seconds_p95": 12.5,
                "planning_duration_seconds_avg": 6.0,
                "confirmation_wait_seconds_p95": 300.0,
            },
            "team_tasks": [{"kind": "produce", "status": "completed", "count": 7}],
            "coordinator_replans": [{"reason_code": "plan_blueprint_violation", "count": 2}],
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
    assert "porthouse_agent_slots_active 3" in text
    assert "porthouse_database_pool_waiting 1" in text
    assert 'porthouse_provider_failure_rate_24h{provider="test",model="test/model"} 10.0' in text
    assert 'porthouse_team_runs_total{status="waiting_input"} 2' in text
    assert 'porthouse_team_plan_actions_total{action="confirmed"} 3' in text
    assert 'porthouse_team_planning_duration_seconds{quantile="p95"} 12.5' in text
    assert "porthouse_team_plan_confirmation_wait_seconds 300.0" in text
    assert 'porthouse_team_tasks_total{kind="produce",status="completed"} 7' in text
    assert 'porthouse_coordinator_replans_total{reason_code="plan_blueprint_violation"} 2' in text


def test_telemetry_is_inert_until_explicitly_enabled(monkeypatch) -> None:
    monkeypatch.delenv("PORTHOUSE_OTEL_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert configure_telemetry(service_name="test-disabled") is False
    with telemetry_span("test.disabled") as span:
        assert span is None
