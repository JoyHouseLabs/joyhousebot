from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.domain.agents import AgentDefinition, AgentRevision
from joyhousebot.providers.usage import cache_hit_usage, normalized_usage
from tests.support.postgres_store import PostgresTestStore


def _record_invocation(
    store: PostgresTestStore,
    *,
    run_id: str,
    suffix: str,
    usage: dict,
    cache_status: str = "miss",
) -> None:
    span = store.start_execution_span(
        span_id=f"span-{suffix}",
        trace_id=f"trace-{suffix}",
        run_id=run_id,
        span_kind="model",
        name="model.generate",
    )
    invocation = store.create_model_invocation(
        invocation_id=f"model-{suffix}",
        run_id=run_id,
        span_id=span.span_id,
        provider="test",
        model="test/model-v1",
        operation="generate",
    )
    store.finish_model_invocation(
        invocation.invocation_id,
        usage=usage,
        cost_usd=float(usage.get("cost_usd") or 0),
        cache_status=cache_status,
    )


def test_usage_api_reads_full_invocation_ledger_and_excludes_other_users(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "model-usage-api.db")
    agent_id = f"usage-default-{uuid4().hex}"
    store.save_agent_revision(
        AgentDefinition(agent_id=agent_id, name="Usage Default", is_default=True),
        AgentRevision(
            revision_id=f"{agent_id}:v1",
            agent_id=agent_id,
            version=1,
            model_policy={"primary": "test/model-v1"},
            status="published",
        ),
    )
    for run_id, user_id in (
        ("usage-run-1", "usage-user"),
        ("usage-run-2", "usage-user"),
        ("usage-run-other", "other-user"),
    ):
        store.create_runtime_run(
            run_id=run_id,
            user_id=user_id,
            session_id=f"session-{run_id}",
            agent_id="default",
            kind="agent",
            prompt="meter",
            options={},
        )
    provider_usage = normalized_usage(
        input_tokens=10,
        output_tokens=2,
        provider_cost_usd=0.01,
    )
    _record_invocation(
        store,
        run_id="usage-run-1",
        suffix="provider",
        usage=provider_usage,
    )
    _record_invocation(
        store,
        run_id="usage-run-2",
        suffix="cache",
        usage=cache_hit_usage(provider_usage),
        cache_status="hit",
    )
    _record_invocation(
        store,
        run_id="usage-run-other",
        suffix="other",
        usage=normalized_usage(
            input_tokens=1_000,
            output_tokens=1_000,
            provider_cost_usd=10,
        ),
    )
    store.create_api_access_token(
        user_id="usage-user",
        actor_id="test",
        token="usage-ledger-token",
    )
    client = TestClient(create_app(build_api_container(config=Config(), store=store)))

    with client:
        response = client.get(
            "/v1/usage",
            headers={"Authorization": "Bearer usage-ledger-token"},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "runs": 2,
        "model_invocations": 2,
        "input_tokens": 20,
        "output_tokens": 4,
        "total_tokens": 24,
        "billed_input_tokens": 10,
        "billed_output_tokens": 2,
        "billed_total_tokens": 12,
        "cost_usd": 0.01,
        "missing_usage_invocations": 0,
        "partial_usage_invocations": 0,
        "missing_billing_invocations": 0,
        "usage_status": "exact",
        "billing_status": "exact",
    }
    overview = store.get_platform_overview()["usage"]
    assert overview["total_tokens"] == 2_024
    assert overview["billed_total_tokens"] == 2_012
    assert overview["cost_usd"] == pytest.approx(10.01)
    provider_metrics = store.operational_metrics()["providers"]
    assert provider_metrics[0]["count"] == 3
    assert provider_metrics[0]["input_tokens"] == 1_020
    assert provider_metrics[0]["billed_input_tokens"] == 1_010
