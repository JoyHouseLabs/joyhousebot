"""RPC handlers for plugin management methods."""

from __future__ import annotations

from typing import Any, Callable


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]


async def try_handle_plugins_method(
    *,
    method: str,
    params: dict[str, Any],
    config: Any,
    app_state: dict[str, Any],
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
) -> RpcResult | None:
    """Handle plugins.* RPC methods. Return None if method is unrelated."""
    manager = app_state.get("plugin_manager")

    if method == "plugins.list":
        if manager is None:
            return True, {"plugins": []}, None
        return True, {"plugins": manager.list_plugins()}, None

    if method == "plugins.info":
        if manager is None:
            return False, None, rpc_error("UNAVAILABLE", "plugin host unavailable", None)
        plugin_id = str(params.get("id") or "").strip()
        if not plugin_id:
            return False, None, rpc_error("INVALID_REQUEST", "plugins.info requires id", None)
        return True, manager.info(plugin_id), None

    if method == "plugins.doctor":
        if manager is None:
            return False, None, rpc_error("UNAVAILABLE", "plugin host unavailable", None)
        return True, manager.doctor(), None

    if method == "plugins.status":
        if manager is None:
            return True, {"ok": False, "plugins": {"total": 0, "loaded": 0, "errored": 0}}, None
        return True, manager.status_report(), None

    if method == "plugins.reload":
        if manager is None:
            return False, None, rpc_error("UNAVAILABLE", "plugin host unavailable", None)
        snapshot = manager.load(
            workspace_dir=str(config.workspace_path),
            config=config.model_dump(by_alias=True),
            reload=True,
        )
        app_state["plugin_snapshot"] = snapshot
        return True, {"ok": True, "plugins": len(snapshot.plugins)}, None

    if method == "plugins.gateway.methods":
        if manager is None:
            return True, {"methods": []}, None
        return True, {"methods": manager.gateway_methods()}, None

    if method == "plugins.http.dispatch":
        if manager is None:
            return False, None, rpc_error("UNAVAILABLE", "plugin manager unavailable", None)
        request_payload = params.get("request")
        request_obj = request_payload if isinstance(request_payload, dict) else params
        result = manager.http_dispatch(request_obj)
        if bool(result.get("ok")):
            return True, result, None
        error_obj = result.get("error")
        if isinstance(error_obj, dict):
            return (
                False,
                None,
                rpc_error(
                    str(error_obj.get("code") or "INTERNAL_ERROR"),
                    str(error_obj.get("message") or "plugin http dispatch failed"),
                    error_obj.get("data") if isinstance(error_obj.get("data"), dict) else None,
                ),
            )
        return False, None, rpc_error("INTERNAL_ERROR", "plugin http dispatch failed", None)

    if method == "plugins.cli.list":
        if manager is None:
            return True, {"commands": []}, None
        return True, {"commands": manager.cli_commands()}, None

    if method == "plugins.cli.invoke":
        if manager is None:
            return False, None, rpc_error("UNAVAILABLE", "plugin manager unavailable", None)
        command = str(params.get("command") or "").strip()
        if not command:
            return False, None, rpc_error("INVALID_REQUEST", "plugins.cli.invoke requires command", None)
        payload = params.get("payload")
        payload_obj = payload if isinstance(payload, dict) else {}
        result = manager.invoke_cli_command(command=command, payload=payload_obj)
        if bool(result.get("ok")):
            return True, result, None
        error_obj = result.get("error")
        if isinstance(error_obj, dict):
            return (
                False,
                None,
                rpc_error(
                    str(error_obj.get("code") or "INTERNAL_ERROR"),
                    str(error_obj.get("message") or "plugin cli invoke failed"),
                    None,
                ),
            )
        return False, None, rpc_error("INTERNAL_ERROR", "plugin cli invoke failed", None)

    if method == "plugins.channels.list":
        if manager is None:
            return True, {"channels": []}, None
        return True, {"channels": manager.channels_list()}, None

    if method == "plugins.providers.list":
        if manager is None:
            return True, {"providers": []}, None
        return True, {"providers": manager.providers_list()}, None

    if method == "plugins.hooks.list":
        if manager is None:
            return True, {"hooks": []}, None
        return True, {"hooks": manager.hooks_list()}, None

    if method == "plugins.services.start":
        if manager is None:
            return False, None, rpc_error("UNAVAILABLE", "plugin manager unavailable", None)
        return True, {"rows": manager.start_services()}, None

    if method == "plugins.services.stop":
        if manager is None:
            return False, None, rpc_error("UNAVAILABLE", "plugin manager unavailable", None)
        return True, {"rows": manager.stop_services()}, None

    if method == "plugins.setup_host":
        if manager is None:
            return False, None, rpc_error("UNAVAILABLE", "plugin manager unavailable", None)
        install_deps = bool(params.get("installDeps", True))
        build_dist = bool(params.get("buildDist", True))
        dry_run = bool(params.get("dryRun", False))
        result = manager.client.setup_host(
            install_deps=install_deps,
            build_dist=build_dist,
            dry_run=dry_run,
        )
        return True, result if isinstance(result, dict) else {}, None

    return None

