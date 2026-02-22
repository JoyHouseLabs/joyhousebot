"""Utilities for sequential RPC handler dispatch pipelines."""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Iterable


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]
HandlerResult = RpcResult | None
DispatchHandler = Callable[[], Awaitable[HandlerResult] | HandlerResult]


async def run_handler_pipeline(handlers: Iterable[DispatchHandler]) -> HandlerResult:
    """Run handlers in order and return the first non-None result."""
    for handler in handlers:
        outcome = handler()
        result = await outcome if inspect.isawaitable(outcome) else outcome
        if result is not None:
            return result
    return None

