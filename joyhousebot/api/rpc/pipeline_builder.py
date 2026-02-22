"""Build ordered RPC dispatch handler pipelines."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from joyhousebot.api.rpc.context_models import RpcDispatchContext, RpcDispatchHandlers
from joyhousebot.api.rpc.dispatch_pipeline import DispatchHandler

def _build_rpc_dispatch_handlers(
    *,
    context: RpcDispatchContext,
    handlers: RpcDispatchHandlers,
) -> tuple[DispatchHandler, ...]:
    """Create ordered handlers tuple for RPC dispatch pipeline."""
    return (
        *_build_control_plane_handlers(context=context, handlers=handlers),
        *_build_sessions_and_config_handlers(context=context, handlers=handlers),
        *_build_runtime_and_ops_handlers(context=context, handlers=handlers),
    )


def _build_control_plane_handlers(
    *,
    context: RpcDispatchContext,
    handlers: RpcDispatchHandlers,
) -> tuple[DispatchHandler, ...]:
    """Build control-plane and chat related dispatch handlers."""
    ctx = context
    h = handlers
    m = ctx.method
    p = ctx.params
    return (
        lambda: h.try_handle_connect_method(
            method=m,
            params=p,
            client=ctx.client,
            connection_key=ctx.connection_key,
            client_host=ctx.client_host,
            config=ctx.config,
            get_connect_nonce=ctx.get_connect_nonce,
            rate_limiter=ctx.rate_limiter,
            load_persistent_state=ctx.load_persistent_state,
            save_persistent_state=ctx.save_persistent_state,
            hash_pairing_token=ctx.hash_pairing_token,
            now_ms=ctx.now_ms,
            resolve_agent=ctx.resolve_agent,
            build_sessions_list_payload=ctx.build_sessions_list_payload,
            control_overview=ctx.control_overview,
            gateway_methods_with_plugins=ctx.gateway_methods_with_plugins,
            gateway_events=ctx.gateway_events,
            presence_entries=ctx.presence_entries,
            normalize_presence_entry=ctx.normalize_presence_entry,
            build_actions_catalog=ctx.build_actions_catalog,
            resolve_canvas_host_url=ctx.resolve_canvas_host_url,
            log_connect=ctx.connect_logger,
        ),
        lambda: h.try_handle_health_status_method(
            method=m,
            params=p,
            control_overview=ctx.control_overview,
            run_rpc_shadow=ctx.run_rpc_shadow,
            load_persistent_state=ctx.load_persistent_state,
        ),
        lambda: h.handle_agents_with_shadow(
            method=m,
            params=p,
            config=ctx.config,
            app_state=ctx.app_state,
            rpc_error=ctx.rpc_error,
            build_agents_list_payload=ctx.build_agents_list_payload,
            normalize_agent_id=ctx.normalize_agent_id,
            ensure_agent_workspace_bootstrap=ctx.ensure_agent_workspace_bootstrap,
            now_ms=ctx.now_ms,
            save_config=ctx.save_config,
            get_cached_config=ctx.get_cached_config,
            run_rpc_shadow=ctx.run_rpc_shadow,
        ),
        lambda: h.try_handle_misc_method(
            method=m,
            params=p,
            config=ctx.config,
            app_state=ctx.app_state,
            rpc_error=ctx.rpc_error,
            get_models_payload=ctx.get_models_payload,
            build_auth_profiles_report=ctx.build_auth_profiles_report,
            build_actions_catalog=ctx.build_actions_catalog,
            validate_action_candidate=ctx.validate_action_candidate,
            validate_action_batch=ctx.validate_action_batch,
            control_overview=ctx.control_overview,
            now_ms=ctx.now_ms,
            get_alerts_lifecycle_view=ctx.get_alerts_lifecycle_view,
            presence_entries=ctx.presence_entries,
            normalize_presence_entry=ctx.normalize_presence_entry,
            get_store=ctx.get_store,
            load_persistent_state=ctx.load_persistent_state,
            run_update_install=ctx.run_update_install,
            create_task=ctx.create_task,
        ),
        lambda: h.try_handle_chat_runtime_method(
            method=m,
            params=p,
            rpc_error=ctx.rpc_error,
            register_agent_job=ctx.register_agent_job,
            get_running_run_id_for_session=ctx.get_running_run_id_for_session,
            complete_agent_job=ctx.complete_agent_job,
            wait_agent_job=ctx.wait_agent_job,
            chat=ctx.chat,
            chat_message_cls=ctx.chat_message_cls,
            resolve_agent=ctx.resolve_agent,
            build_chat_history_payload=ctx.build_chat_history_payload,
            now_iso=ctx.now_iso,
            now_ms=ctx.now_ms,
            emit_event=ctx.emit_event,
            fanout_chat_to_subscribed_nodes=ctx.fanout_chat_to_subscribed_nodes,
            lane_can_run=getattr(ctx, "lane_can_run", None),
            lane_enqueue=getattr(ctx, "lane_enqueue", None),
            persist_trace=getattr(ctx, "persist_trace", None),
            request_abort=getattr(ctx, "request_abort", None),
        ),
        lambda: h.try_handle_lanes_method(
            method=m,
            params=p,
            app_state=ctx.app_state,
            now_ms=ctx.now_ms,
            rpc_error=ctx.rpc_error,
        ),
        lambda: h.try_handle_traces_method(
            method=m,
            params=p,
            get_store=ctx.get_store,
            rpc_error=ctx.rpc_error,
        ),
    )


def _build_sessions_and_config_handlers(
    *,
    context: RpcDispatchContext,
    handlers: RpcDispatchHandlers,
) -> tuple[DispatchHandler, ...]:
    """Build session/config/plugin/control dispatch handlers."""
    ctx = context
    h = handlers
    m = ctx.method
    p = ctx.params
    return (
        lambda: h.handle_sessions_usage_with_shadow(
            method=m,
            params=p,
            config=ctx.config,
            rpc_error=ctx.rpc_error,
            resolve_agent=ctx.resolve_agent,
            build_sessions_list_payload=ctx.build_sessions_list_payload,
            build_chat_history_payload=ctx.build_chat_history_payload,
            apply_session_patch=ctx.apply_session_patch,
            now_ms=ctx.now_ms,
            delete_session=ctx.delete_session,
            empty_usage_totals=ctx.empty_usage_totals,
            session_usage_entry=ctx.session_usage_entry,
            estimate_tokens=ctx.estimate_tokens,
            run_rpc_shadow=ctx.run_rpc_shadow,
        ),
        lambda: h.handle_config_with_shadow(
            method=m,
            params=p,
            config=ctx.config,
            rpc_error=ctx.rpc_error,
            build_config_snapshot=ctx.build_config_snapshot,
            build_config_schema_payload=ctx.build_config_schema_payload,
            apply_config_from_raw=ctx.apply_config_from_raw,
            get_cached_config=ctx.get_cached_config,
            update_config=ctx.update_config,
            config_update_cls=ctx.config_update_cls,
            run_rpc_shadow=ctx.run_rpc_shadow,
        ),
        lambda: h.try_handle_plugins_method(
            method=m,
            params=p,
            config=ctx.config,
            app_state=ctx.app_state,
            rpc_error=ctx.rpc_error,
        ),
        lambda: h.try_handle_control_state_method(
            method=m,
            params=p,
            config=ctx.config,
            app_state=ctx.app_state,
            emit_event=ctx.emit_event,
            rpc_error=ctx.rpc_error,
            load_persistent_state=ctx.load_persistent_state,
            save_persistent_state=ctx.save_persistent_state,
            now_ms=ctx.now_ms,
            build_skills_status_report=ctx.build_skills_status_report,
            build_channels_status_snapshot=ctx.build_channels_status_snapshot,
            get_cached_config=ctx.get_cached_config,
            save_config=ctx.save_config,
        ),
        lambda: h.try_handle_web_login_method(
            method=m,
            params=p,
            config=ctx.config,
            save_persistent_state=ctx.save_persistent_state,
            now_ms=ctx.now_ms,
            rpc_error=ctx.rpc_error,
        ),
    )


def _build_runtime_and_ops_handlers(
    *,
    context: RpcDispatchContext,
    handlers: RpcDispatchHandlers,
) -> tuple[DispatchHandler, ...]:
    """Build node/browser/exec/cron and plugin gateway dispatch handlers."""
    ctx = context
    h = handlers
    m = ctx.method
    p = ctx.params
    client_id = ctx.client.client_id
    return (
        lambda: h.try_handle_pairing_method(
            method=m,
            params=p,
            client_id=client_id,
            rpc_error=ctx.rpc_error,
            load_persistent_state=ctx.load_persistent_state,
            save_persistent_state=ctx.save_persistent_state,
            load_device_pairs_state=ctx.load_device_pairs_state,
            hash_pairing_token=ctx.hash_pairing_token,
            now_ms=ctx.now_ms,
            broadcast_rpc_event=ctx.broadcast_rpc_event,
        ),
        lambda: h.try_handle_node_runtime_method(
            method=m,
            params=p,
            client_id=client_id,
            app_state=ctx.app_state,
            node_registry=ctx.node_registry,
            config=ctx.config,
            rpc_error=ctx.rpc_error,
            load_device_pairs_state=ctx.load_device_pairs_state,
            save_persistent_state=ctx.save_persistent_state,
            now_ms=ctx.now_ms,
            resolve_node_command_allowlist=ctx.resolve_node_command_allowlist,
            is_node_command_allowed=ctx.is_node_command_allowed,
            normalize_node_event_payload=ctx.normalize_node_event_payload,
            run_node_agent_request=ctx.run_node_agent_request,
            get_store=ctx.get_store,
            broadcast_rpc_event=ctx.broadcast_rpc_event,
        ),
        lambda: h.try_handle_browser_method(
            method=m,
            params=p,
            config=ctx.config,
            node_registry=ctx.node_registry,
            rpc_error=ctx.rpc_error,
            resolve_browser_node=ctx.resolve_browser_node,
            resolve_node_command_allowlist=ctx.resolve_node_command_allowlist,
            is_node_command_allowed=ctx.is_node_command_allowed,
            persist_browser_proxy_files=ctx.persist_browser_proxy_files,
            apply_browser_proxy_paths=ctx.apply_browser_proxy_paths,
            browser_control_url=ctx.browser_control_url,
        ),
        lambda: h.try_handle_exec_approval_method(
            method=m,
            params=p,
            app_state=ctx.app_state,
            client_id=client_id,
            rpc_error=ctx.rpc_error,
            cleanup_expired_exec_approvals=ctx.cleanup_expired_exec_approvals,
            now_ms=ctx.now_ms,
            broadcast_rpc_event=ctx.broadcast_rpc_event,
            load_persistent_state=ctx.load_persistent_state,
            save_persistent_state=ctx.save_persistent_state,
        ),
        lambda: h.try_handle_sandbox_method(
            method=m,
            params=p,
            rpc_error=ctx.rpc_error,
            load_persistent_state=ctx.load_persistent_state,
            save_persistent_state=ctx.save_persistent_state,
        ),
        lambda: h.try_handle_cron_method(
            method=m,
            params=p,
            app_state=ctx.app_state,
            rpc_error=ctx.rpc_error,
            now_ms=ctx.now_ms,
            load_persistent_state=ctx.load_persistent_state,
            save_persistent_state=ctx.save_persistent_state,
            cron_list_jobs=ctx.cron_list_jobs,
            cron_add_job=ctx.cron_add_job,
            cron_patch_job=ctx.cron_patch_job,
            cron_delete_job=ctx.cron_delete_job,
            cron_run_job=ctx.cron_run_job,
            build_cron_add_body=lambda payload: ctx.build_cron_add_body_from_params(
                payload,
                cron_job_create_cls=ctx.cron_job_create_cls,
                cron_schedule_body_cls=ctx.cron_schedule_body_cls,
            ),
            build_cron_patch_body=lambda payload: ctx.build_cron_patch_body_from_params(
                payload,
                cron_job_patch_cls=ctx.cron_job_patch_cls,
            ),
            emit_event=ctx.emit_event,
        ),
        lambda: h.try_handle_plugin_gateway_method(
            method=m,
            params=p,
            app_state=ctx.app_state,
            rpc_error=ctx.rpc_error,
            plugin_gateway_methods=ctx.plugin_gateway_methods,
        ),
    )


def build_rpc_dispatch_handlers_from_context(
    *,
    context: RpcDispatchContext,
    handlers: RpcDispatchHandlers,
) -> tuple[DispatchHandler, ...]:
    """Build dispatch handlers from grouped context and handler registries."""
    return _build_rpc_dispatch_handlers(context=context, handlers=handlers)

