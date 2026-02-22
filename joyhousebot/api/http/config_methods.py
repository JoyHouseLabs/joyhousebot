"""Helpers for config-related HTTP endpoint payloads."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException

from joyhousebot.services.config.config_service import (
    apply_config_update,
    build_http_config_payload,
)


def get_config_response(
    *,
    config: Any,
    get_wallet_from_store: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    return build_http_config_payload(config=config, get_wallet_payload=get_wallet_from_store)


def update_config_response(
    *,
    update: Any,
    get_cached_config: Callable[..., Any],
    save_config: Callable[[Any], None],
    app_state: dict[str, Any],
    get_wallet_from_store: Callable[[], dict[str, Any]],
    log_plugin_reload_warning: Callable[[str], None],
) -> dict[str, Any]:
    def update_app_config(cfg: Any) -> None:
        app_state["config"] = cfg

    def plugin_reloader(cfg: Any) -> None:
        plugin_manager = app_state.get("plugin_manager")
        if plugin_manager is None:
            return
        try:
            app_state["plugin_snapshot"] = plugin_manager.load(
                workspace_dir=str(cfg.workspace_path),
                config=cfg.model_dump(by_alias=True),
                reload=True,
            )
        except Exception as exc:
            log_plugin_reload_warning(str(exc))

    try:
        apply_config_update(
            update=update,
            get_config=get_cached_config,
            save_config=save_config,
            update_app_config=update_app_config,
            plugin_reloader=plugin_reloader,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "message": "Configuration updated", "wallet": get_wallet_from_store()}
