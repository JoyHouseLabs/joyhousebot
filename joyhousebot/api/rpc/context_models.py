"""Shared dataclass models for RPC dispatch context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(slots=True)
class RpcDispatchHandlers:
    """RPC method handler callables used by the dispatch pipeline."""

    try_handle_connect_method: Callable[..., Awaitable[Any]]
    try_handle_health_status_method: Callable[..., Awaitable[Any]]
    handle_agents_with_shadow: Callable[..., Awaitable[Any]]
    try_handle_misc_method: Callable[..., Awaitable[Any]]
    try_handle_chat_runtime_method: Callable[..., Awaitable[Any]]
    handle_sessions_usage_with_shadow: Callable[..., Awaitable[Any]]
    handle_config_with_shadow: Callable[..., Awaitable[Any]]
    try_handle_plugins_method: Callable[..., Awaitable[Any]]
    try_handle_control_state_method: Callable[..., Awaitable[Any]]
    try_handle_web_login_method: Callable[..., Awaitable[Any]]
    try_handle_pairing_method: Callable[..., Awaitable[Any]]
    try_handle_node_runtime_method: Callable[..., Awaitable[Any]]
    try_handle_browser_method: Callable[..., Awaitable[Any]]
    try_handle_exec_approval_method: Callable[..., Awaitable[Any]]
    try_handle_sandbox_method: Callable[..., Awaitable[Any]]
    try_handle_cron_method: Callable[..., Awaitable[Any]]
    try_handle_plugin_gateway_method: Callable[..., Any]
    try_handle_lanes_method: Callable[..., Awaitable[Any]]
    try_handle_traces_method: Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class RpcDispatchContext:
    """Request-scoped and shared dependencies for building dispatch pipeline."""

    method: str
    params: dict[str, Any]
    client: Any
    connection_key: str
    client_host: str | None
    config: Any
    app_state: dict[str, Any]
    get_connect_nonce: Callable[[str], str | None]
    rate_limiter: Any
    node_registry: Any
    emit_event: Callable[[str, Any], Awaitable[None]] | None
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]]
    broadcast_rpc_event: Callable[[str, Any, set[str] | None], Awaitable[None]]
    connect_logger: Callable[[str, list[str], str], None]
    browser_control_url: str
    resolve_agent: Callable[[Any], Any | None]
    build_sessions_list_payload: Callable[[Any, Any], dict[str, Any]]
    control_overview: Callable[[], Awaitable[dict[str, Any]]]
    gateway_methods_with_plugins: Callable[[], list[str]]
    gateway_events: list[str]
    presence_entries: Callable[[], list[Any]]
    normalize_presence_entry: Callable[[Any], dict[str, Any]]
    build_actions_catalog: Callable[[], Any]
    now_ms: Callable[[], int]
    resolve_canvas_host_url: Callable[[Any], str]
    run_rpc_shadow: Callable[[str, dict[str, Any], Any], Awaitable[None]]
    build_agents_list_payload: Callable[[Any, Any], dict[str, Any]]
    normalize_agent_id: Callable[[Any], str]
    ensure_agent_workspace_bootstrap: Callable[[str], None]
    save_config: Callable[[Any], None]
    get_cached_config: Callable[..., Any]
    get_models_payload: Callable[[], dict[str, Any]]
    build_auth_profiles_report: Callable[[], dict[str, Any]]
    validate_action_candidate: Callable[[dict[str, Any]], tuple[bool, str | None]]
    validate_action_batch: Callable[[list[dict[str, Any]]], tuple[bool, str | None]]
    get_alerts_lifecycle_view: Callable[[], dict[str, Any]]
    get_store: Callable[[], Any]
    load_persistent_state: Callable[[str, Any], Any]
    run_update_install: Callable[[], Awaitable[None]]
    create_task: Callable[[Any], Any]
    register_agent_job: Callable[[str, str | None], bool]
    get_running_run_id_for_session: Callable[[str], str | None]
    complete_agent_job: Callable[..., None]
    wait_agent_job: Callable[..., Awaitable[dict[str, Any] | None]]
    chat: Callable[[Any], Awaitable[dict[str, Any]]]
    chat_message_cls: type
    build_chat_history_payload: Callable[[Any, int], dict[str, Any]]
    now_iso: Callable[[], str]
    fanout_chat_to_subscribed_nodes: Callable[..., Awaitable[None]]
    apply_session_patch: Callable[[Any, Any], tuple[bool, str | None]]
    delete_session: Callable[[str, Any], None]
    empty_usage_totals: Callable[[], dict[str, Any]]
    session_usage_entry: Callable[..., dict[str, Any]]
    estimate_tokens: Callable[[str], int]
    build_config_snapshot: Callable[[Any], dict[str, Any]]
    build_config_schema_payload: Callable[[], dict[str, Any]]
    apply_config_from_raw: Callable[[dict[str, Any], Any], None]
    update_config: Callable[[Any], Any]
    config_update_cls: type
    save_persistent_state: Callable[[str, Any], None]
    build_skills_status_report: Callable[[], dict[str, Any]]
    build_channels_status_snapshot: Callable[[], dict[str, Any]]
    load_device_pairs_state: Callable[[], dict[str, Any]]
    hash_pairing_token: Callable[[str], str]
    resolve_node_command_allowlist: Callable[[Any, Any], list[str] | None]
    is_node_command_allowed: Callable[[str, list[str], list[str] | None], tuple[bool, str]]
    normalize_node_event_payload: Callable[[dict[str, Any]], tuple[dict[str, Any] | None, str | None]]
    run_node_agent_request: Callable[..., Awaitable[tuple[bool, str]]]
    resolve_browser_node: Callable[[list[Any], str], Any | None]
    persist_browser_proxy_files: Callable[[list[dict[str, Any]] | None], dict[str, str]]
    apply_browser_proxy_paths: Callable[[dict[str, Any], dict[str, str]], None]
    cleanup_expired_exec_approvals: Callable[[], None]
    cron_list_jobs: Callable[..., Awaitable[dict[str, Any]]]
    cron_add_job: Callable[[Any], Awaitable[dict[str, Any]]]
    cron_patch_job: Callable[[str, Any], Awaitable[dict[str, Any]]]
    cron_delete_job: Callable[[str], Awaitable[dict[str, Any]]]
    cron_run_job: Callable[..., Awaitable[dict[str, Any]]]
    build_cron_add_body_from_params: Callable[..., Any]
    cron_job_create_cls: type
    cron_schedule_body_cls: type
    build_cron_patch_body_from_params: Callable[..., Any]
    cron_job_patch_cls: type
    plugin_gateway_methods: Callable[[], list[str]]
    lane_can_run: Callable[[str], bool] | None = None
    lane_enqueue: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None
    persist_trace: Callable[[str, str, str, str | None], None] | None = None
    check_abort_requested: Callable[[str], bool] | None = None
    request_abort: Callable[[str], None] | None = None

