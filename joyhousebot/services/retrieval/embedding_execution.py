"""Budgeted, rate-limited execution of immutable embedding profiles."""

from __future__ import annotations

import asyncio
import math
import uuid
from dataclasses import dataclass
from typing import Any


class EmbeddingAdmissionError(RuntimeError):
    """The frozen profile denied an embedding request before provider execution."""


@dataclass(frozen=True, slots=True)
class EmbeddingExecutionResult:
    embeddings: list[list[float]]
    input_tokens: int
    request_count: int
    cost_usd: float
    operation_id: str


def conservative_token_estimate(texts: list[str]) -> int:
    """Return a provider-neutral upper estimate suitable for preflight admission."""
    return sum(max(1, math.ceil(len(text.encode("utf-8")) / 2)) for text in texts)


async def execute_embedding_profile(
    *,
    store: Any,
    repository: Any,
    provider_resolver: Any,
    profile: dict[str, Any],
    texts: list[str],
    user_id: str,
    doc_id: str | None,
    revision_id: str | None,
    operation_type: str,
    run_id: str | None = None,
    task_id: str | None = None,
    eval_run_id: str | None = None,
    eval_case_id: str | None = None,
) -> EmbeddingExecutionResult:
    """Execute batches under the exact Profile's cost and cluster rate limits."""
    configuration = dict(profile["configuration"])
    operation_id = f"kemb_{uuid.uuid4().hex}"
    provider = None
    embeddings: list[list[float]] = []
    total_tokens = 0
    total_cost = 0.0
    request_count = 0
    status = "succeeded"
    error_code: str | None = None
    try:
        policy = await asyncio.to_thread(
            store.get_embedding_model_policy,
            configuration["provider_id"],
            configuration["provider_revision_id"],
            configuration["model_id"],
        )
        price = policy["input_cost_per_million_tokens"]
        if price is None:
            raise EmbeddingAdmissionError("embedding model pricing is undeclared")
        provider = provider_resolver(configuration)
        if asyncio.iscoroutine(provider):
            provider = await provider
        batch_size = int(configuration["batch_size"])
        for offset in range(0, len(texts), batch_size):
            batch = texts[offset : offset + batch_size]
            estimated_tokens = conservative_token_estimate(batch)
            if estimated_tokens > int(configuration["max_input_tokens"]):
                raise EmbeddingAdmissionError(
                    "embedding batch exceeds the Profile max_input_tokens boundary"
                )
            estimated_cost = estimated_tokens * float(price) / 1_000_000
            if total_cost + estimated_cost > float(configuration["max_cost_usd"]):
                raise EmbeddingAdmissionError(
                    "embedding operation exceeds the Profile max_cost_usd boundary"
                )
            admitted = await asyncio.to_thread(
                store.check_embedding_rate_limit,
                profile["revision_id"],
                requests=1,
                input_tokens=estimated_tokens,
                requests_per_minute=int(configuration.get("requests_per_minute") or 60),
                tokens_per_minute=int(
                    configuration.get("tokens_per_minute") or 1_000_000
                ),
            )
            if not admitted:
                raise EmbeddingAdmissionError("embedding Profile rate limit exceeded")
            request_count += 1
            response = await provider.embed(
                batch,
                model=configuration["model_id"],
                dimensions=int(configuration["dimensions"]),
            )
            expected_dimensions = int(configuration["dimensions"])
            batch_embeddings = list(response.embeddings)
            if len(batch_embeddings) != len(batch):
                raise RuntimeError(
                    "embedding provider result count does not match the input batch"
                )
            if any(
                len(vector) != expected_dimensions
                or any(not math.isfinite(float(value)) for value in vector)
                for vector in batch_embeddings
            ):
                raise RuntimeError(
                    "embedding provider returned invalid dimensions or non-finite values"
                )
            actual_tokens = int(response.usage.get("input_tokens") or estimated_tokens)
            actual_cost = actual_tokens * float(price) / 1_000_000
            total_tokens += actual_tokens
            total_cost += actual_cost
            if actual_tokens > estimated_tokens:
                admitted = await asyncio.to_thread(
                    store.check_embedding_rate_limit,
                    profile["revision_id"],
                    requests=0,
                    input_tokens=actual_tokens - estimated_tokens,
                    requests_per_minute=int(
                        configuration.get("requests_per_minute") or 60
                    ),
                    tokens_per_minute=int(
                        configuration.get("tokens_per_minute") or 1_000_000
                    ),
                )
                if not admitted:
                    raise EmbeddingAdmissionError(
                        "provider usage exceeded the Profile rate limit"
                    )
            if actual_tokens > int(configuration["max_input_tokens"]):
                raise EmbeddingAdmissionError(
                    "provider usage exceeded the Profile max_input_tokens boundary"
                )
            if total_cost > float(configuration["max_cost_usd"]):
                raise EmbeddingAdmissionError(
                    "provider usage exceeded the Profile max_cost_usd boundary"
                )
            embeddings.extend(batch_embeddings)
    except Exception as exc:
        status = "failed"
        error_code = type(exc).__name__
        raise
    finally:
        try:
            await asyncio.to_thread(
                repository.record_embedding_usage,
                operation_id=operation_id,
                user_id=user_id,
                doc_id=doc_id,
                revision_id=revision_id,
                run_id=run_id,
                task_id=task_id,
                eval_run_id=eval_run_id,
                eval_case_id=eval_case_id,
                embedding_profile_id=profile["revision_id"],
                operation_type=operation_type,
                status=status,
                request_count=request_count,
                input_tokens=total_tokens,
                cost_usd=total_cost,
                error_code=error_code,
            )
        finally:
            close = getattr(provider, "close", None) if provider is not None else None
            if callable(close):
                closed = close()
                if asyncio.iscoroutine(closed):
                    await closed
    return EmbeddingExecutionResult(
        embeddings=embeddings,
        input_tokens=total_tokens,
        request_count=request_count,
        cost_usd=total_cost,
        operation_id=operation_id,
    )


__all__ = [
    "EmbeddingAdmissionError",
    "EmbeddingExecutionResult",
    "conservative_token_estimate",
    "execute_embedding_profile",
]
