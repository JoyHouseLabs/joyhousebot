"""RPC handler for plugin gateway passthrough methods."""

from __future__ import annotations

from typing import Any, Callable, Iterable


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


def try_handle_plugin_gateway_method(
    *,
    method: str,
    params: dict[str, Any],
    app_state: dict[str, Any],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    plugin_gateway_methods: Callable[[], Iterable[str]],
) -> RpcResult | None:
    """Handle plugin gateway passthrough and return None when unmatched."""
    plugin_manager = app_state.get("plugin_manager")
    if plugin_manager is None or method not in set(plugin_gateway_methods()):
        return None

    try:
        plugin_result = plugin_manager.invoke_gateway_method(method=method, params=params)
        if bool(plugin_result.get("ok", False)):
            return True, plugin_result.get("payload"), None
        return False, None, plugin_result.get("error") or rpc_error(
            "INTERNAL_ERROR",
            f"plugin method failed: {method}",
            None,
        )
    except Exception as exc:
        return False, None, rpc_error("INTERNAL_ERROR", f"plugin method error: {exc}", None)

