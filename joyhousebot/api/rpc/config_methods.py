"""RPC handlers for config.* methods."""

from __future__ import annotations

from typing import Any, Awaitable, Callable


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_config_method(
    *,
    method: str,
    params: dict[str, Any],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    build_config_snapshot: Callable[[Any], dict[str, Any]],
    build_config_schema_payload: Callable[[], dict[str, Any]],
    apply_config_from_raw: Callable[[str], tuple[bool, str | None]],
    get_cached_config: Callable[..., Any],
    update_config: Callable[[Any], Awaitable[dict[str, Any]]],
    config_update_cls: type,
    config: Any,
) -> RpcResult | None:
    """Handle config.* RPC methods. Return None when method is unrelated."""
    if method == "config.get":
        payload = build_config_snapshot(config)
        return True, payload, None

    if method == "config.schema":
        return True, build_config_schema_payload(), None

    if method in {"config.patch", "config.set", "config.apply"}:
        if method in {"config.set", "config.apply"} and isinstance(params.get("raw"), str):
            ok, err = apply_config_from_raw(str(params.get("raw")))
            if not ok:
                return False, None, rpc_error("INVALID_REQUEST", f"invalid config raw: {err}", None)
            cfg = get_cached_config(force_reload=True)
            payload = build_config_snapshot(cfg)
            return True, payload, None

        payload = await update_config(config_update_cls(**params))
        cfg = get_cached_config(force_reload=True)
        return (
            True,
            {
                "ok": True,
                "updated": True,
                "wallet": payload.get("wallet"),
                "hash": build_config_snapshot(cfg)["hash"],
            },
            None,
        )

    return None

