"""RPC request guard helpers for validation and authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class RpcRequestGuardResult:
    """Prepared request context after validation and guard checks."""

    method: str | None
    params: dict[str, Any]
    config: Any | None
    node_registry: Any | None
    error: dict[str, Any] | None


def prepare_rpc_request_context(
    *,
    req: dict[str, Any],
    client: Any,
    connection_key: str,
    app_state: dict[str, Any],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    get_cached_config: Callable[[], Any],
    node_registry_cls: type,
    is_method_allowed_by_canary: Callable[[str, Any], bool],
    authorize_rpc_method: Callable[[str, Any, Any], dict[str, Any] | None],
    log_denied: Callable[[str, str, list[str], str], None],
) -> RpcRequestGuardResult:
    """Validate request shape and apply gateway/canary/auth guards."""
    req_id = req.get("id")
    if req.get("type") != "req" or not isinstance(req_id, str) or not req_id:
        return RpcRequestGuardResult(
            method=None,
            params={},
            config=None,
            node_registry=None,
            error=rpc_error("INVALID_REQUEST", "invalid request frame", None),
        )

    method = req.get("method")
    if not isinstance(method, str) or not method:
        return RpcRequestGuardResult(
            method=None,
            params={},
            config=None,
            node_registry=None,
            error=rpc_error("INVALID_REQUEST", "request.method is required", None),
        )
    params = req.get("params") if isinstance(req.get("params"), dict) else {}

    config = app_state.get("config") or get_cached_config()
    node_registry = app_state.get("node_registry") or node_registry_cls()
    app_state["node_registry"] = node_registry

    if not getattr(config.gateway, "rpc_enabled", True):
        return RpcRequestGuardResult(
            method=method,
            params=params,
            config=config,
            node_registry=node_registry,
            error=rpc_error("UNAVAILABLE", "rpc gateway is disabled", None),
        )
    if not is_method_allowed_by_canary(method, config):
        return RpcRequestGuardResult(
            method=method,
            params=params,
            config=config,
            node_registry=node_registry,
            error=rpc_error("UNAVAILABLE", f"method gated by canary: {method}", None),
        )

    auth_error = authorize_rpc_method(method, client, config)
    if auth_error:
        log_denied(method, str(getattr(client, "role", "unknown")), sorted(getattr(client, "scopes", set())), str(getattr(client, "client_id", "") or connection_key))
        return RpcRequestGuardResult(
            method=method,
            params=params,
            config=config,
            node_registry=node_registry,
            error=auth_error,
        )

    return RpcRequestGuardResult(
        method=method,
        params=params,
        config=config,
        node_registry=node_registry,
        error=None,
    )

