from __future__ import annotations

import pytest

from porthouse.providers.usage import (
    cache_hit_usage,
    missing_usage,
    normalized_usage,
    partial_usage,
)
from porthouse.runtime.models import AgentOptions, AgentUsage
from porthouse.runtime.planning_loop import _budget_reason


def test_catalog_pricing_preserves_logical_and_billed_usage() -> None:
    usage = normalized_usage(
        input_tokens=1_000,
        output_tokens=200,
        cached_input_tokens=400,
        reasoning_output_tokens=50,
        pricing={
            "model_id": "provider/model",
            "provider_revision_id": "revision-1",
            "input_cost_per_million_tokens": 2,
            "cached_input_cost_per_million_tokens": 0.5,
            "output_cost_per_million_tokens": 10,
        },
    )

    assert usage["total_tokens"] == 1_200
    assert usage["billed_total_tokens"] == 1_200
    assert usage["cached_input_tokens"] == 400
    assert usage["reasoning_output_tokens"] == 50
    assert usage["cost_usd"] == pytest.approx(0.0034)
    assert usage["cost_source"] == "catalog"
    assert usage["pricing"]["provider_revision_id"] == "revision-1"


def test_cache_hit_keeps_workload_but_is_not_billed_again() -> None:
    usage = cache_hit_usage(
        normalized_usage(
            input_tokens=40,
            output_tokens=10,
            provider_cost_usd=0.02,
        )
    )

    assert usage["total_tokens"] == 50
    assert usage["billed_total_tokens"] == 0
    assert usage["cost_usd"] == 0
    assert usage["usage_source"] == "cache"
    assert usage["billing_status"] == "not_billed"
    assert usage["cost_source"] == "cache"


def test_missing_and_partial_usage_are_not_reported_as_exact_zero() -> None:
    missing = missing_usage()
    assert missing["usage_status"] == "missing"
    assert missing["billing_status"] == "missing"
    assert partial_usage(missing)["usage_status"] == "missing"
    priced_missing = normalized_usage(
        usage_source="missing",
        usage_status="missing",
        pricing={"input_cost_per_million_tokens": 1},
    )
    assert "cost_usd" not in priced_missing
    assert priced_missing["billing_status"] == "missing"

    partial = partial_usage(normalized_usage(input_tokens=12, output_tokens=0))
    assert partial["input_tokens"] == 12
    assert partial["usage_status"] == "partial"


def test_agent_usage_aggregates_billed_tokens_and_completeness() -> None:
    aggregate = AgentUsage()
    aggregate.add(AgentUsage.from_dict(AgentUsage().to_dict()))
    aggregate.add(
        AgentUsage.from_dict(
            normalized_usage(input_tokens=20, output_tokens=5, provider_cost_usd=0.01)
        )
    )
    aggregate.add(AgentUsage.from_dict(cache_hit_usage({"input_tokens": 20, "output_tokens": 5})))
    aggregate.add(AgentUsage.from_dict(missing_usage()))

    assert aggregate.total_tokens == 50
    assert aggregate.billed_total_tokens == 25
    assert aggregate.cost_usd == pytest.approx(0.01)
    assert aggregate.model_invocations == 3
    assert aggregate.missing_usage_invocations == 1
    assert aggregate.usage_status == "partial"
    assert aggregate.billing_status == "partial"


def test_cost_budget_fails_closed_when_billing_is_missing() -> None:
    usage = AgentUsage.from_dict(normalized_usage(input_tokens=20, output_tokens=5))
    assert _budget_reason(AgentOptions(prompt="plan", max_cost_usd=1), usage) == (
        "maximum cost budget cannot be enforced because planning billing is missing"
    )
