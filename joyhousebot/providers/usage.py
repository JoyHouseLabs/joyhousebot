"""Provider-neutral model usage and billing semantics."""

from __future__ import annotations

from typing import Any


def _tokens(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _money(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def normalized_usage(
    *,
    input_tokens: Any = 0,
    output_tokens: Any = 0,
    billed_input_tokens: Any | None = None,
    billed_output_tokens: Any | None = None,
    cached_input_tokens: Any = 0,
    cache_creation_input_tokens: Any = 0,
    reasoning_output_tokens: Any = 0,
    audio_input_tokens: Any = 0,
    audio_output_tokens: Any = 0,
    usage_source: str = "provider",
    usage_status: str = "exact",
    billing_status: str | None = None,
    provider_cost_usd: Any = None,
    pricing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable dual logical/billed usage contract.

    The compatibility fields ``input_tokens``/``output_tokens`` remain logical
    workload. ``billed_*`` is the provider-facing meter for the current call.
    """

    logical_input = _tokens(input_tokens)
    logical_output = _tokens(output_tokens)
    billed_input = (
        logical_input if billed_input_tokens is None else _tokens(billed_input_tokens)
    )
    billed_output = (
        logical_output if billed_output_tokens is None else _tokens(billed_output_tokens)
    )
    cached_input = min(_tokens(cached_input_tokens), logical_input)
    cache_creation_input = min(_tokens(cache_creation_input_tokens), logical_input)
    reasoning_output = min(_tokens(reasoning_output_tokens), logical_output)
    audio_input = min(_tokens(audio_input_tokens), logical_input)
    audio_output = min(_tokens(audio_output_tokens), logical_output)
    status = usage_status if usage_status in {"exact", "partial", "missing"} else "partial"
    source = (
        usage_source
        if usage_source in {"provider", "cache", "estimated", "missing"}
        else "missing"
    )

    usage: dict[str, Any] = {
        "input_tokens": logical_input,
        "output_tokens": logical_output,
        "total_tokens": logical_input + logical_output,
        "billed_input_tokens": billed_input,
        "billed_output_tokens": billed_output,
        "billed_total_tokens": billed_input + billed_output,
        "cached_input_tokens": cached_input,
        "cache_creation_input_tokens": cache_creation_input,
        "reasoning_output_tokens": reasoning_output,
        "audio_input_tokens": audio_input,
        "audio_output_tokens": audio_output,
        "usage_source": source,
        "usage_status": status,
    }
    if source == "cache":
        usage["cost_usd"] = 0.0
        usage["billing_status"] = "not_billed"
        usage["cost_source"] = "cache"
        return usage

    snapshot = _pricing_snapshot(pricing or {})
    if snapshot:
        usage["pricing"] = snapshot
    provider_cost = _money(provider_cost_usd)
    if provider_cost is not None:
        usage["cost_usd"] = provider_cost
        usage["billing_status"] = "exact" if status == "exact" else "partial"
        usage["cost_source"] = "provider"
        return usage

    calculated_cost = (
        None
        if status == "missing"
        else _calculate_cost(
            usage,
            pricing=pricing or {},
        )
    )
    if calculated_cost is not None:
        usage["cost_usd"] = calculated_cost
        usage["billing_status"] = "exact" if status == "exact" else "partial"
        usage["cost_source"] = "catalog"
    else:
        usage["billing_status"] = billing_status or (
            "missing" if source != "cache" else "not_billed"
        )
        usage["cost_source"] = "missing" if source != "cache" else "cache"
    return usage


def missing_usage() -> dict[str, Any]:
    return normalized_usage(
        usage_source="missing",
        usage_status="missing",
        billed_input_tokens=0,
        billed_output_tokens=0,
        billing_status="missing",
    )


def partial_usage(value: dict[str, Any] | None) -> dict[str, Any]:
    if not value:
        return missing_usage()
    result = dict(value)
    if result.get("usage_status") == "missing":
        return result
    result["usage_status"] = "partial"
    if result.get("billing_status") == "exact":
        result["billing_status"] = "partial"
    return result


def cache_hit_usage(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value or {}
    usage = normalized_usage(
        input_tokens=source.get("input_tokens", source.get("prompt_tokens", 0)),
        output_tokens=source.get("output_tokens", source.get("completion_tokens", 0)),
        billed_input_tokens=0,
        billed_output_tokens=0,
        cached_input_tokens=source.get("cached_input_tokens", 0),
        cache_creation_input_tokens=source.get("cache_creation_input_tokens", 0),
        reasoning_output_tokens=source.get("reasoning_output_tokens", 0),
        audio_input_tokens=source.get("audio_input_tokens", 0),
        audio_output_tokens=source.get("audio_output_tokens", 0),
        usage_source="cache",
        usage_status=("exact" if source.get("usage_status") != "missing" else "missing"),
        billing_status="not_billed",
    )
    if isinstance(source.get("pricing"), dict):
        usage["pricing"] = dict(source["pricing"])
    return usage


def _pricing_snapshot(pricing: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "model_id",
        "provider_revision_id",
        "input_cost_per_million_tokens",
        "output_cost_per_million_tokens",
        "cached_input_cost_per_million_tokens",
        "cache_creation_input_cost_per_million_tokens",
    }
    return {key: pricing[key] for key in sorted(allowed) if pricing.get(key) is not None}


def _calculate_cost(usage: dict[str, Any], *, pricing: dict[str, Any]) -> float | None:
    if not pricing:
        return None
    input_rate = _money(pricing.get("input_cost_per_million_tokens"))
    output_rate = _money(pricing.get("output_cost_per_million_tokens"))
    cached_rate = _money(pricing.get("cached_input_cost_per_million_tokens"))
    creation_rate = _money(pricing.get("cache_creation_input_cost_per_million_tokens"))
    billed_input = _tokens(usage.get("billed_input_tokens"))
    billed_output = _tokens(usage.get("billed_output_tokens"))
    cached = min(_tokens(usage.get("cached_input_tokens")), billed_input)
    creation = min(
        _tokens(usage.get("cache_creation_input_tokens")), max(0, billed_input - cached)
    )
    uncached = max(0, billed_input - cached - creation)
    if billed_output and output_rate is None:
        return None
    if uncached and input_rate is None:
        return None
    if cached and cached_rate is None:
        return None
    if creation and creation_rate is None:
        return None
    total = (
        uncached * (input_rate or 0)
        + cached * (cached_rate or 0)
        + creation * (creation_rate or 0)
        + billed_output * (output_rate or 0)
    ) / 1_000_000
    return max(0.0, total)


__all__ = [
    "cache_hit_usage",
    "missing_usage",
    "normalized_usage",
    "partial_usage",
]
