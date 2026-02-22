"""Shared post-processing hooks for RPC handler results."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Iterable


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def apply_shadow_hook_if_needed(
    *,
    method: str,
    params: dict[str, Any],
    result: RpcResult,
    shadow_methods: Iterable[str],
    run_rpc_shadow: Callable[[str, dict[str, Any], Any], Awaitable[None]],
) -> RpcResult:
    """Run RPC shadow callback for selected methods when result is successful."""
    ok, payload, _err = result
    if method in set(shadow_methods) and ok and payload is not None:
        await run_rpc_shadow(method, params, payload)
    return result

