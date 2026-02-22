"""Shared retry policy helpers for bridge operations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TypeVar
from collections.abc import Awaitable, Callable

T = TypeVar("T")


@dataclass(slots=True)
class RetryPolicy:
    """Simple exponential-backoff retry policy."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 5.0


async def with_retry(fn: Callable[[], Awaitable[T]], policy: RetryPolicy) -> T:
    """Run async callable with retries and bounded exponential backoff."""
    if policy.max_attempts <= 1:
        return await fn()
    last_exc: Exception | None = None
    for attempt in range(policy.max_attempts):
        try:
            return await fn()
        except Exception as exc:  # pragma: no cover - generic passthrough guard
            last_exc = exc
            if attempt >= policy.max_attempts - 1:
                break
            delay = min(policy.max_delay_seconds, policy.base_delay_seconds * (2**attempt))
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc

