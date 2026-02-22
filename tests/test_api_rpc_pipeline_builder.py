import pytest

from joyhousebot.api.rpc.context_models import RpcDispatchContext, RpcDispatchHandlers
from joyhousebot.api.rpc.pipeline_builder import build_rpc_dispatch_handlers_from_context


def test_build_rpc_dispatch_handlers_from_context_returns_ordered_handlers():
    async def _none_async(**_kwargs):
        return None

    def _none_sync(**_kwargs):
        return None

    ctx = _build_dummy_context()
    handlers = RpcDispatchHandlers(
        try_handle_connect_method=_none_async,
        try_handle_health_status_method=_none_async,
        handle_agents_with_shadow=_none_async,
        try_handle_misc_method=_none_async,
        try_handle_chat_runtime_method=_none_async,
        handle_sessions_usage_with_shadow=_none_async,
        handle_config_with_shadow=_none_async,
        try_handle_plugins_method=_none_async,
        try_handle_control_state_method=_none_async,
        try_handle_web_login_method=_none_async,
        try_handle_pairing_method=_none_async,
        try_handle_node_runtime_method=_none_async,
        try_handle_browser_method=_none_async,
        try_handle_exec_approval_method=_none_async,
        try_handle_sandbox_method=_none_async,
        try_handle_cron_method=_none_async,
        try_handle_plugin_gateway_method=_none_sync,
        try_handle_lanes_method=_none_async,
        try_handle_traces_method=_none_async,
    )
    result = build_rpc_dispatch_handlers_from_context(context=ctx, handlers=handlers)
    assert isinstance(result, tuple)
    assert len(result) == 19


@pytest.mark.asyncio
async def test_pipeline_builder_preserves_handler_order():
    calls: list[str] = []

    async def _mark_async(name: str):
        calls.append(name)
        return None

    def _mark_sync(name: str):
        calls.append(name)
        return None

    ctx = _build_dummy_context()
    handlers = RpcDispatchHandlers(
        try_handle_connect_method=lambda **_kwargs: _mark_async("connect"),
        try_handle_health_status_method=lambda **_kwargs: _mark_async("health_status"),
        handle_agents_with_shadow=lambda **_kwargs: _mark_async("agents"),
        try_handle_misc_method=lambda **_kwargs: _mark_async("misc"),
        try_handle_chat_runtime_method=lambda **_kwargs: _mark_async("chat_runtime"),
        handle_sessions_usage_with_shadow=lambda **_kwargs: _mark_async("sessions_usage"),
        handle_config_with_shadow=lambda **_kwargs: _mark_async("config"),
        try_handle_plugins_method=lambda **_kwargs: _mark_async("plugins"),
        try_handle_control_state_method=lambda **_kwargs: _mark_async("control_state"),
        try_handle_web_login_method=lambda **_kwargs: _mark_async("web_login"),
        try_handle_pairing_method=lambda **_kwargs: _mark_async("pairing"),
        try_handle_node_runtime_method=lambda **_kwargs: _mark_async("node_runtime"),
        try_handle_browser_method=lambda **_kwargs: _mark_async("browser"),
        try_handle_exec_approval_method=lambda **_kwargs: _mark_async("exec_approval"),
        try_handle_sandbox_method=lambda **_kwargs: _mark_async("sandbox"),
        try_handle_cron_method=lambda **_kwargs: _mark_async("cron"),
        try_handle_plugin_gateway_method=lambda **_kwargs: _mark_sync("plugin_gateway"),
        try_handle_lanes_method=lambda **_kwargs: _mark_async("lanes"),
        try_handle_traces_method=lambda **_kwargs: _mark_async("traces"),
    )
    pipeline = build_rpc_dispatch_handlers_from_context(context=ctx, handlers=handlers)
    for step in pipeline:
        outcome = step()
        if hasattr(outcome, "__await__"):
            await outcome
    assert calls == [
        "connect",
        "health_status",
        "agents",
        "misc",
        "chat_runtime",
        "lanes",
        "traces",
        "sessions_usage",
        "config",
        "plugins",
        "control_state",
        "web_login",
        "pairing",
        "node_runtime",
        "browser",
        "exec_approval",
        "sandbox",
        "cron",
        "plugin_gateway",
    ]


def _build_dummy_context() -> RpcDispatchContext:
    async def _none_async(**_kwargs):
        return None

    return RpcDispatchContext(
        method="x",
        params={},
        client=type("C", (), {"client_id": "c1"})(),
        connection_key="k1",
        config=object(),
        app_state={},
        node_registry=object(),
        emit_event=None,
        rpc_error=lambda *_: {},
        broadcast_rpc_event=lambda *_args, **_kwargs: _none_async(),
        connect_logger=lambda *_: None,
        browser_control_url="",
        resolve_agent=lambda *_: None,
        build_sessions_list_payload=lambda *_: {"sessions": []},
        control_overview=lambda: _none_async(),
        gateway_methods_with_plugins=lambda: [],
        gateway_events=[],
        presence_entries=lambda: [],
        normalize_presence_entry=lambda e: e,
        build_actions_catalog=lambda: {},
        now_ms=lambda: 1,
        resolve_canvas_host_url=lambda *_: "",
        run_rpc_shadow=lambda *_args, **_kwargs: _none_async(),
        build_agents_list_payload=lambda *_: {"agents": []},
        normalize_agent_id=lambda x: str(x),
        ensure_agent_workspace_bootstrap=lambda *_: None,
        save_config=lambda *_: None,
        get_cached_config=lambda *_: object(),
        get_models_payload=lambda: {},
        build_auth_profiles_report=lambda: {},
        validate_action_candidate=lambda *_: (True, None),
        validate_action_batch=lambda *_: (True, None),
        get_alerts_lifecycle_view=lambda: {},
        get_store=lambda: object(),
        load_persistent_state=lambda *_: {},
        run_update_install=lambda: _none_async(),
        create_task=lambda *_: None,
        register_agent_job=lambda *_: True,
        get_running_run_id_for_session=lambda _sk: None,
        complete_agent_job=lambda *_args, **_kwargs: None,
        wait_agent_job=lambda *_args, **_kwargs: _none_async(),
        chat=lambda *_args, **_kwargs: _none_async(),
        chat_message_cls=dict,
        build_chat_history_payload=lambda *_: {},
        now_iso=lambda: "",
        fanout_chat_to_subscribed_nodes=lambda *_args, **_kwargs: _none_async(),
        apply_session_patch=lambda *_: (True, None),
        delete_session=lambda *_: None,
        empty_usage_totals=lambda: {},
        session_usage_entry=lambda *_: {},
        estimate_tokens=lambda *_: 0,
        build_config_snapshot=lambda *_: {},
        build_config_schema_payload=lambda: {},
        apply_config_from_raw=lambda *_: None,
        update_config=lambda *_: object(),
        config_update_cls=dict,
        save_persistent_state=lambda *_: None,
        build_skills_status_report=lambda: {},
        build_channels_status_snapshot=lambda: {},
        load_device_pairs_state=lambda: {},
        hash_pairing_token=lambda s: s,
        resolve_node_command_allowlist=lambda *_: None,
        is_node_command_allowed=lambda *_: (True, ""),
        normalize_node_event_payload=lambda *_: ({}, None),
        run_node_agent_request=lambda *_args, **_kwargs: _none_async(),
        resolve_browser_node=lambda *_: None,
        persist_browser_proxy_files=lambda *_: {},
        apply_browser_proxy_paths=lambda *_: None,
        cleanup_expired_exec_approvals=lambda: None,
        cron_list_jobs=lambda *_args, **_kwargs: _none_async(),
        cron_add_job=lambda *_args, **_kwargs: _none_async(),
        cron_patch_job=lambda *_args, **_kwargs: _none_async(),
        cron_delete_job=lambda *_args, **_kwargs: _none_async(),
        cron_run_job=lambda *_args, **_kwargs: _none_async(),
        build_cron_add_body_from_params=lambda *_args, **_kwargs: {},
        cron_job_create_cls=dict,
        cron_schedule_body_cls=dict,
        build_cron_patch_body_from_params=lambda *_args, **_kwargs: {},
        cron_job_patch_cls=dict,
        plugin_gateway_methods=lambda: [],
    )

