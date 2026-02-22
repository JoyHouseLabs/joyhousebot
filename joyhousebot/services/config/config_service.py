"""Shared config domain operations for HTTP/RPC adapters."""

from __future__ import annotations

from typing import Any, Callable


def apply_wallet_update(config: Any, update: Any) -> None:
    """Apply wallet part of config update. Raises ValueError on validation failure."""
    if getattr(update, "wallet", None) is None:
        return
    from joyhousebot.config.schema import WalletConfig
    from joyhousebot.identity.wallet_store import (
        clear_wallet_file,
        create_and_save_wallet,
        validate_wallet_password,
        wallet_file_exists,
    )

    w = update.wallet
    enabled = bool(w.get("enabled") if isinstance(w, dict) else getattr(w, "enabled", False))
    if enabled:
        password = (w.get("password") or "").strip() if isinstance(w, dict) else (getattr(w, "password", None) or "").strip()
        if password:
            try:
                validate_wallet_password(password)
            except ValueError as exc:
                raise ValueError(str(exc))
            address = create_and_save_wallet(password)
            config.wallet = WalletConfig(enabled=True, address=address)
        elif not wallet_file_exists():
            raise ValueError("启用钱包须设置密码（不少于8位，且包含大小写字母）")
    else:
        clear_wallet_file()
        config.wallet = WalletConfig(enabled=False, address="")


def apply_config_update(
    *,
    update: Any,
    get_config: Callable[..., Any],
    save_config: Callable[[Any], None],
    update_app_config: Callable[[Any], None],
    plugin_reloader: Callable[[Any], None] | None = None,
) -> None:
    """Apply a full config update: domain merge, wallet, save, app_state update, plugin reload.
    Raises ValueError on validation (e.g. wallet password). Caller may map to HTTP 400.
    """
    config = get_config(force_reload=True)
    apply_config_update_to_domain(config=config, update=update)
    apply_wallet_update(config=config, update=update)
    save_config(config)
    update_app_config(config)
    if plugin_reloader:
        plugin_reloader(config)


def build_http_config_payload(
    *,
    config: Any,
    get_wallet_payload: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "agents": {
                "defaults": {
                    "model": config.agents.defaults.model,
                    "model_fallbacks": list(config.agents.defaults.model_fallbacks),
                    "provider": getattr(config.agents.defaults, "provider", "") or "",
                    "temperature": config.agents.defaults.temperature,
                    "max_tokens": config.agents.defaults.max_tokens,
                    "max_tool_iterations": config.agents.defaults.max_tool_iterations,
                    "memory_window": config.agents.defaults.memory_window,
                    "max_context_tokens": getattr(config.agents.defaults, "max_context_tokens", None),
                    "workspace": config.agents.defaults.workspace,
                },
                "list": _build_agents_list_payload(config),
                "default_id": config.agents.default_id,
            },
            "providers": {
                "custom": {"api_key": config.providers.custom.api_key, "api_base": config.providers.custom.api_base},
                "anthropic": {"api_key": config.providers.anthropic.api_key, "api_base": config.providers.anthropic.api_base},
                "openai": {"api_key": config.providers.openai.api_key, "api_base": config.providers.openai.api_base},
                "openrouter": {
                    "api_key": config.providers.openrouter.api_key,
                    "api_base": config.providers.openrouter.api_base,
                },
                "deepseek": {"api_key": config.providers.deepseek.api_key, "api_base": config.providers.deepseek.api_base},
                "groq": {"api_key": config.providers.groq.api_key, "api_base": config.providers.groq.api_base},
                "zhipu": {"api_key": config.providers.zhipu.api_key, "api_base": config.providers.zhipu.api_base},
                "gemini": {"api_key": config.providers.gemini.api_key, "api_base": config.providers.gemini.api_base},
                "moonshot": {"api_key": config.providers.moonshot.api_key, "api_base": config.providers.moonshot.api_base},
                "minimax": {"api_key": config.providers.minimax.api_key, "api_base": config.providers.minimax.api_base},
                "aihubmix": {"api_key": config.providers.aihubmix.api_key, "api_base": config.providers.aihubmix.api_base},
            },
            "auth": {
                "profiles": {
                    k: {
                        "provider": v.provider,
                        "mode": v.mode,
                        "enabled": v.enabled,
                        "api_key": v.api_key,
                        "token": v.token,
                        "api_base": v.api_base,
                        "extra_headers": v.extra_headers,
                    }
                    for k, v in (config.auth.profiles or {}).items()
                },
                "order": config.auth.order,
                "cooldowns": {
                    "billing_backoff_hours": config.auth.cooldowns.billing_backoff_hours,
                    "billing_backoff_hours_by_provider": config.auth.cooldowns.billing_backoff_hours_by_provider,
                    "billing_max_hours": config.auth.cooldowns.billing_max_hours,
                    "failure_window_hours": config.auth.cooldowns.failure_window_hours,
                },
            },
            "channels": {
                "whatsapp": {
                    "enabled": config.channels.whatsapp.enabled,
                    "bridge_url": config.channels.whatsapp.bridge_url,
                    "bridge_token": config.channels.whatsapp.bridge_token,
                },
                "telegram": {
                    "enabled": config.channels.telegram.enabled,
                    "token": config.channels.telegram.token,
                    "proxy": config.channels.telegram.proxy,
                },
                "feishu": {
                    "enabled": config.channels.feishu.enabled,
                    "app_id": config.channels.feishu.app_id,
                    "app_secret": config.channels.feishu.app_secret,
                },
                "dingtalk": {
                    "enabled": config.channels.dingtalk.enabled,
                    "client_id": config.channels.dingtalk.client_id,
                    "client_secret": config.channels.dingtalk.client_secret,
                },
                "discord": {"enabled": config.channels.discord.enabled, "token": config.channels.discord.token},
                "email": {
                    "enabled": config.channels.email.enabled,
                    "imap_host": config.channels.email.imap_host,
                    "imap_username": config.channels.email.imap_username,
                    "smtp_host": config.channels.email.smtp_host,
                    "smtp_username": config.channels.email.smtp_username,
                    "from_address": config.channels.email.from_address,
                },
                "slack": {
                    "enabled": config.channels.slack.enabled,
                    "bot_token": config.channels.slack.bot_token,
                    "app_token": config.channels.slack.app_token,
                },
                "qq": {
                    "enabled": config.channels.qq.enabled,
                    "app_id": config.channels.qq.app_id,
                    "secret": config.channels.qq.secret,
                },
            },
            "tools": {
                "web": {
                    "search": {
                        "api_key": config.tools.web.search.api_key,
                        "max_results": config.tools.web.search.max_results,
                    }
                },
                "exec": {
                    "timeout": config.tools.exec.timeout,
                    "shell_mode": config.tools.exec.shell_mode,
                    "container_enabled": getattr(config.tools.exec, "container_enabled", False),
                    "container_image": getattr(config.tools.exec, "container_image", "alpine:3.18"),
                    "container_workspace_mount": getattr(config.tools.exec, "container_workspace_mount", "") or "",
                    "container_user": getattr(config.tools.exec, "container_user", "") or "",
                    "container_network": getattr(config.tools.exec, "container_network", "none") or "none",
                },
                "code_runner": {
                    "enabled": config.tools.code_runner.enabled,
                    "default_backend": config.tools.code_runner.default_backend,
                    "default_mode": config.tools.code_runner.default_mode,
                    "timeout": config.tools.code_runner.timeout,
                    "require_approval": config.tools.code_runner.require_approval,
                    "claude_code_command": config.tools.code_runner.claude_code_command,
                    "container_image": getattr(config.tools.code_runner, "container_image", "") or "",
                    "container_workspace_mount": getattr(config.tools.code_runner, "container_workspace_mount", "") or "",
                    "container_user": getattr(config.tools.code_runner, "container_user", "") or "",
                    "container_network": getattr(config.tools.code_runner, "container_network", "none") or "none",
                },
                "retrieval": {
                    "vector_enabled": config.tools.retrieval.vector_enabled,
                    "vector_threshold_chunks": config.tools.retrieval.vector_threshold_chunks,
                    "embedding_provider": config.tools.retrieval.embedding_provider,
                    "embedding_model": config.tools.retrieval.embedding_model,
                    "vector_backend": config.tools.retrieval.vector_backend,
                    "memory_backend": getattr(config.tools.retrieval, "memory_backend", "builtin"),
                    "memory_use_l0": getattr(config.tools.retrieval, "memory_use_l0", False),
                    "memory_first": getattr(config.tools.retrieval, "memory_first", False),
                    "memory_top_k": getattr(config.tools.retrieval, "memory_top_k", 10),
                },
                "ingest": {
                    "pdf_processing": config.tools.ingest.pdf_processing,
                    "image_processing": config.tools.ingest.image_processing,
                    "url_processing": config.tools.ingest.url_processing,
                    "youtube_processing": config.tools.ingest.youtube_processing,
                    "cloud_ocr_provider": config.tools.ingest.cloud_ocr_provider,
                    "cloud_ocr_api_key": config.tools.ingest.cloud_ocr_api_key,
                },
                "restrict_to_workspace": config.tools.restrict_to_workspace,
                "optional_allowlist": list(config.tools.optional_allowlist or []),
                "mcp_servers": {
                    k: {
                        "command": v.command,
                        "args": list(v.args or []),
                        "env": dict(v.env or {}),
                        "url": v.url or "",
                    }
                    for k, v in (config.tools.mcp_servers or {}).items()
                },
            },
            "gateway": {
                "host": config.gateway.host,
                "port": config.gateway.port,
                "control_token": getattr(config.gateway, "control_token", "") or "",
                "control_password": getattr(config.gateway, "control_password", "") or "",
                "control_ui": _build_gateway_control_ui_payload(config.gateway),
                "rpc_enabled": config.gateway.rpc_enabled,
                "rpc_canary_methods": list(config.gateway.rpc_canary_methods or []),
                "rpc_shadow_reads": config.gateway.rpc_shadow_reads,
                "rpc_default_scopes": list(config.gateway.rpc_default_scopes or []),
                "node_allow_commands": list(getattr(config.gateway, "node_allow_commands", None) or []),
                "node_deny_commands": list(getattr(config.gateway, "node_deny_commands", None) or []),
                "node_browser_mode": getattr(config.gateway, "node_browser_mode", "auto") or "auto",
                "node_browser_target": getattr(config.gateway, "node_browser_target", "") or "",
                "chat_session_serialization": getattr(config.gateway, "chat_session_serialization", True),
                "max_lane_pending": getattr(config.gateway, "max_lane_pending", 100),
                "trace_max_step_payload_chars": getattr(config.gateway, "trace_max_step_payload_chars", 2000),
            },
            "workspace_path": str(config.workspace_path),
            "provider_name": config.get_provider_name(),
            "skills": {"entries": {k: {"enabled": v.enabled, "env": v.env or {}} for k, v in (config.skills.entries or {}).items()}},
            "apps": {
                "enabled": list(getattr(getattr(config, "apps", None), "enabled", None) or []),
            },
            "plugins": {
                "enabled": config.plugins.enabled,
                "openclaw_dir": getattr(config.plugins, "openclaw_dir", "") or "",
                "allow": list(config.plugins.allow or []),
                "deny": list(config.plugins.deny or []),
                "load": {"paths": list(config.plugins.load.paths or [])},
                "entries": {
                    k: {"enabled": v.enabled, "config": dict(v.config or {})}
                    for k, v in (config.plugins.entries or {}).items()
                },
                "slots": {"memory": config.plugins.slots.memory},
                "installs": {
                    k: {
                        "source": v.source,
                        "spec": v.spec,
                        "source_path": v.source_path,
                        "install_path": v.install_path,
                        "version": v.version,
                        "installed_at": v.installed_at,
                    }
                    for k, v in (config.plugins.installs or {}).items()
                },
            },
            "approvals": _build_approvals_payload(config),
            "browser": _build_browser_payload(config),
            "messages": _build_messages_payload(config),
            "commands": _build_commands_payload(config),
            "env": _build_env_payload(config),
            "wallet": get_wallet_payload(),
        },
    }


def _build_agents_list_payload(config: Any) -> list[dict[str, Any]]:
    """Serialize agents.agent_list for HTTP (alias 'list')."""
    lst = getattr(config.agents, "agent_list", None) or getattr(config.agents, "list", None)
    if not lst:
        return []
    out = []
    for e in lst:
        out.append({
            "id": getattr(e, "id", "") or "",
            "name": getattr(e, "name", "") or "",
            "workspace": getattr(e, "workspace", "") or "",
            "model": getattr(e, "model", "") or "",
            "model_fallbacks": list(getattr(e, "model_fallbacks", None) or []),
            "provider": getattr(e, "provider", "") or "",
            "max_tokens": getattr(e, "max_tokens", 8192),
            "temperature": getattr(e, "temperature", 0.7),
            "max_tool_iterations": getattr(e, "max_tool_iterations", 20),
            "memory_window": getattr(e, "memory_window", 50),
            "max_context_tokens": getattr(e, "max_context_tokens", None),
            "default": getattr(e, "default", False),
            "activated": getattr(e, "activated", True),
        })
    return out


def _build_gateway_control_ui_payload(gateway: Any) -> dict[str, Any]:
    """Serialize gateway.control_ui for HTTP."""
    ui = getattr(gateway, "control_ui", None)
    if ui is None:
        return {}
    return {
        "allow_insecure_auth": getattr(ui, "allow_insecure_auth", False),
    }


def _build_approvals_payload(config: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    approvals = getattr(config, "approvals", None)
    if approvals is None:
        return out
    exec_cfg = getattr(approvals, "exec", None)
    if exec_cfg is None:
        return out
    out["exec"] = {
        "enabled": exec_cfg.enabled,
        "mode": getattr(exec_cfg, "mode", "session"),
        "agent_filter": list(exec_cfg.agent_filter or []) if hasattr(exec_cfg, "agent_filter") else None,
        "session_filter": list(exec_cfg.session_filter or []) if hasattr(exec_cfg, "session_filter") else None,
        "targets": [
            {"channel": t.channel, "to": t.to, "account_id": getattr(t, "account_id"), "thread_id": getattr(t, "thread_id")}
            for t in (exec_cfg.targets or [])
        ] if hasattr(exec_cfg, "targets") and exec_cfg.targets else None,
    }
    return out


def _build_browser_payload(config: Any) -> dict[str, Any]:
    browser = getattr(config, "browser", None)
    if browser is None:
        return {}
    return {
        "enabled": getattr(browser, "enabled", True),
        "default_profile": getattr(browser, "default_profile", "default") or "default",
        "profiles": {
            k: {"cdp_port": getattr(v, "cdp_port", 0), "cdp_url": getattr(v, "cdp_url", "") or "", "color": getattr(v, "color", "") or ""}
            for k, v in (getattr(browser, "profiles", None) or {}).items()
        },
        "executable_path": getattr(browser, "executable_path", "") or "",
        "headless": getattr(browser, "headless", False),
    }


def _build_messages_payload(config: Any) -> dict[str, Any]:
    messages = getattr(config, "messages", None)
    if messages is None:
        return {}
    return {
        "ack_reaction_scope": getattr(messages, "ack_reaction_scope", None),
        "ack_reaction": getattr(messages, "ack_reaction", None),
        "remove_ack_after_reply": getattr(messages, "remove_ack_after_reply", None),
        "response_prefix": getattr(messages, "response_prefix", None),
        "suppress_tool_errors": getattr(messages, "suppress_tool_errors", None),
        "after_tool_results_prompt": getattr(messages, "after_tool_results_prompt", None),
        "group_chat": getattr(messages, "group_chat", None),
    }


def _build_commands_payload(config: Any) -> dict[str, Any]:
    commands = getattr(config, "commands", None)
    if commands is None:
        return {}
    return {
        "native": getattr(commands, "native", "auto"),
        "native_skills": getattr(commands, "native_skills", "auto"),
    }


def _build_env_payload(config: Any) -> dict[str, Any]:
    env = getattr(config, "env", None)
    if env is None:
        return {}
    vars_dict = getattr(env, "vars", None)
    return {"vars": dict(vars_dict) if vars_dict else {}}


def apply_config_update_to_domain(*, config: Any, update: Any) -> None:
    if update.providers:
        for provider_name, provider_config in update.providers.items():
            if hasattr(config.providers, provider_name):
                provider = getattr(config.providers, provider_name)
                for key, value in provider_config.items():
                    if hasattr(provider, key):
                        setattr(provider, key, value)
    if update.agents:
        from joyhousebot.config.schema import AgentEntry

        for key, value in update.agents.items():
            if key == "defaults":
                for default_key, default_value in value.items():
                    if hasattr(config.agents.defaults, default_key):
                        setattr(config.agents.defaults, default_key, default_value)
            elif key == "default_id":
                config.agents.default_id = value
            elif key in ("list", "agent_list") and isinstance(value, list):
                config.agents.agent_list = []
                for entry in value:
                    if isinstance(entry, dict):
                        config.agents.agent_list.append(AgentEntry(
                            id=str(entry.get("id") or ""),
                            name=str(entry.get("name") or ""),
                            workspace=str(entry.get("workspace") or ""),
                            model=str(entry.get("model") or ""),
                            model_fallbacks=list(entry.get("model_fallbacks") or []),
                            provider=str(entry.get("provider") or ""),
                            max_tokens=int(entry.get("max_tokens", 8192)),
                            temperature=float(entry.get("temperature", 0.7)),
                            max_tool_iterations=int(entry.get("max_tool_iterations", 20)),
                            memory_window=int(entry.get("memory_window", 50)),
                            max_context_tokens=entry.get("max_context_tokens"),
                            default=bool(entry.get("default", False)),
                            activated=bool(entry.get("activated", True)),
                        ))
    if update.channels:
        for channel_name, channel_config in update.channels.items():
            if hasattr(config.channels, channel_name):
                channel = getattr(config.channels, channel_name)
                for key, value in channel_config.items():
                    if hasattr(channel, key):
                        setattr(channel, key, value)
    if update.tools:
        for key, value in update.tools.items():
            if key == "web":
                # Support both tools.web.search (nested) and flat tools.web with api_key/max_results
                search = value.get("search", value) if isinstance(value, dict) else value
                if isinstance(search, dict):
                    if hasattr(config.tools.web.search, "api_key") and "api_key" in search:
                        config.tools.web.search.api_key = search["api_key"]
                    if hasattr(config.tools.web.search, "max_results") and "max_results" in search:
                        config.tools.web.search.max_results = search["max_results"]
            elif key == "exec":
                if hasattr(config.tools.exec, "timeout") and "timeout" in value:
                    config.tools.exec.timeout = value["timeout"]
                if hasattr(config.tools.exec, "shell_mode") and "shell_mode" in value:
                    config.tools.exec.shell_mode = value["shell_mode"]
                if hasattr(config.tools.exec, "container_enabled") and "container_enabled" in value:
                    config.tools.exec.container_enabled = bool(value["container_enabled"])
                if hasattr(config.tools.exec, "container_image") and "container_image" in value:
                    config.tools.exec.container_image = str(value["container_image"] or "alpine:3.18")
                if hasattr(config.tools.exec, "container_workspace_mount") and "container_workspace_mount" in value:
                    config.tools.exec.container_workspace_mount = str(value["container_workspace_mount"] or "")
                if hasattr(config.tools.exec, "container_user") and "container_user" in value:
                    config.tools.exec.container_user = str(value["container_user"] or "")
                if hasattr(config.tools.exec, "container_network") and "container_network" in value:
                    config.tools.exec.container_network = str(value["container_network"] or "none")
            elif key == "restrict_to_workspace":
                config.tools.restrict_to_workspace = value
            elif key == "code_runner" and isinstance(value, dict):
                cr = config.tools.code_runner
                for fk, fv in value.items():
                    if hasattr(cr, fk):
                        setattr(cr, fk, fv)
            elif key == "retrieval" and isinstance(value, dict):
                r = config.tools.retrieval
                for fk, fv in value.items():
                    if hasattr(r, fk):
                        setattr(r, fk, fv)
            elif key == "ingest" and isinstance(value, dict):
                ing = config.tools.ingest
                for fk, fv in value.items():
                    if hasattr(ing, fk):
                        setattr(ing, fk, fv)
            elif key == "optional_allowlist" and isinstance(value, list):
                config.tools.optional_allowlist = [str(x) for x in value]
            elif key == "mcp_servers" and isinstance(value, dict):
                from joyhousebot.config.schema import MCPServerConfig
                config.tools.mcp_servers = {}
                for name, entry in value.items():
                    if isinstance(entry, dict):
                        config.tools.mcp_servers[str(name)] = MCPServerConfig(
                            command=str(entry.get("command", "") or ""),
                            args=list(entry.get("args") or []),
                            env=dict(entry.get("env") or {}),
                            url=str(entry.get("url") or ""),
                        )
    if update.gateway:
        from joyhousebot.config.schema import GatewayControlUiConfig

        for key, value in update.gateway.items():
            if key == "control_ui" and isinstance(value, dict):
                config.gateway.control_ui = GatewayControlUiConfig(
                    allow_insecure_auth=bool(value.get("allow_insecure_auth", False)),
                )
            elif hasattr(config.gateway, key):
                setattr(config.gateway, key, value)
    if update.skills:
        from joyhousebot.config.schema import SkillEntryConfig

        entries = update.skills.get("entries") or {}
        for name, entry in entries.items():
            if isinstance(entry, dict):
                if config.skills.entries is None:
                    config.skills.entries = {}
                env_raw = entry.get("env")
                env_clean = {str(k): str(v) for k, v in (env_raw or {}).items() if isinstance(k, str) and isinstance(v, str)} or None
                config.skills.entries[name] = SkillEntryConfig(enabled=entry.get("enabled", True), env=env_clean)
    if update.apps:
        apps = update.apps
        if isinstance(apps.get("enabled"), list):
            config.apps.enabled = [str(x) for x in apps["enabled"]]
    if update.plugins:
        from joyhousebot.config.schema import PluginEntryConfig, PluginInstallRecord

        plugins = update.plugins
        if "enabled" in plugins:
            config.plugins.enabled = bool(plugins.get("enabled"))
        if "openclaw_dir" in plugins:
            config.plugins.openclaw_dir = str(plugins.get("openclaw_dir") or "")
        if isinstance(plugins.get("allow"), list):
            config.plugins.allow = [str(x) for x in plugins.get("allow", [])]
        if isinstance(plugins.get("deny"), list):
            config.plugins.deny = [str(x) for x in plugins.get("deny", [])]
        load_cfg = plugins.get("load")
        if isinstance(load_cfg, dict) and isinstance(load_cfg.get("paths"), list):
            config.plugins.load.paths = [str(x) for x in load_cfg.get("paths", [])]
        entries = plugins.get("entries")
        if isinstance(entries, dict):
            for plugin_id, entry in entries.items():
                if isinstance(entry, dict):
                    config.plugins.entries[str(plugin_id)] = PluginEntryConfig(
                        enabled=bool(entry.get("enabled", True)),
                        config=entry.get("config") if isinstance(entry.get("config"), dict) else {},
                    )
        slots = plugins.get("slots")
        if isinstance(slots, dict):
            memory_slot = slots.get("memory")
            config.plugins.slots.memory = str(memory_slot) if memory_slot is not None else None
        installs = plugins.get("installs")
        if isinstance(installs, dict):
            for plugin_id, entry in installs.items():
                if isinstance(entry, dict):
                    config.plugins.installs[str(plugin_id)] = PluginInstallRecord(
                        source=str(entry.get("source") or ""),
                        spec=str(entry.get("spec") or ""),
                        source_path=str(entry.get("source_path") or entry.get("sourcePath") or ""),
                        install_path=str(entry.get("install_path") or entry.get("installPath") or ""),
                        version=str(entry.get("version") or ""),
                        installed_at=str(entry.get("installed_at") or entry.get("installedAt") or ""),
                    )
    if update.auth:
        from joyhousebot.config.schema import AuthProfileConfig, AuthCooldownsConfig
        auth = update.auth
        if "profiles" in auth and isinstance(auth["profiles"], dict):
            if config.auth.profiles is None:
                config.auth.profiles = {}
            for k, v in auth["profiles"].items():
                if isinstance(v, dict):
                    config.auth.profiles[str(k)] = AuthProfileConfig(
                        provider=str(v.get("provider", "") or ""),
                        mode=str(v.get("mode", "api_key") or "api_key"),
                        enabled=bool(v.get("enabled", True)),
                        api_key=str(v.get("api_key", "") or ""),
                        token=str(v.get("token", "") or ""),
                        api_base=v.get("api_base"),
                        extra_headers=dict(v.get("extra_headers") or {}),
                    )
        if "order" in auth and isinstance(auth["order"], dict):
            config.auth.order = auth["order"]
        if "cooldowns" in auth and isinstance(auth["cooldowns"], dict):
            co = auth["cooldowns"]
            config.auth.cooldowns.billing_backoff_hours = co.get("billing_backoff_hours", config.auth.cooldowns.billing_backoff_hours)
            config.auth.cooldowns.billing_max_hours = co.get("billing_max_hours", config.auth.cooldowns.billing_max_hours)
            config.auth.cooldowns.failure_window_hours = co.get("failure_window_hours", config.auth.cooldowns.failure_window_hours)
            if isinstance(co.get("billing_backoff_hours_by_provider"), dict):
                config.auth.cooldowns.billing_backoff_hours_by_provider = co["billing_backoff_hours_by_provider"]
    if update.approvals:
        from joyhousebot.config.schema import ApprovalsConfig, ApprovalsExecConfig, ApprovalsExecTargetConfig
        appr = update.approvals
        exec_cfg = appr.get("exec") if isinstance(appr, dict) else None
        if isinstance(exec_cfg, dict):
            targets = exec_cfg.get("targets")
            target_list = None
            if isinstance(targets, list):
                target_list = [
                    ApprovalsExecTargetConfig(
                        channel=str(t.get("channel", "") or ""),
                        to=str(t.get("to", "") or ""),
                        account_id=t.get("account_id"),
                        thread_id=t.get("thread_id"),
                    )
                    for t in targets if isinstance(t, dict)
                ]
            config.approvals = ApprovalsConfig(
                exec=ApprovalsExecConfig(
                    enabled=bool(exec_cfg.get("enabled", False)),
                    mode=exec_cfg.get("mode", "session") or "session",
                    agent_filter=exec_cfg.get("agent_filter") if isinstance(exec_cfg.get("agent_filter"), list) else None,
                    session_filter=exec_cfg.get("session_filter") if isinstance(exec_cfg.get("session_filter"), list) else None,
                    targets=target_list,
                )
            )
    if update.browser and isinstance(update.browser, dict):
        from joyhousebot.config.schema import BrowserConfig, BrowserProfileConfig
        b = update.browser
        config.browser.enabled = b.get("enabled", config.browser.enabled)
        config.browser.default_profile = str(b.get("default_profile") or config.browser.default_profile or "default")
        config.browser.executable_path = str(b.get("executable_path") or "")
        config.browser.headless = bool(b.get("headless", False))
        if isinstance(b.get("profiles"), dict):
            config.browser.profiles = {
                str(k): BrowserProfileConfig(
                    cdp_port=int(v.get("cdp_port", 0)) if isinstance(v, dict) else 0,
                    cdp_url=str(v.get("cdp_url", "") or "") if isinstance(v, dict) else "",
                    color=str(v.get("color", "") or "") if isinstance(v, dict) else "",
                )
                for k, v in b["profiles"].items()
            }
    if update.messages and isinstance(update.messages, dict):
        from joyhousebot.config.schema import MessagesConfig
        m = update.messages
        config.messages = MessagesConfig(
            ack_reaction_scope=m.get("ack_reaction_scope"),
            ack_reaction=m.get("ack_reaction"),
            remove_ack_after_reply=m.get("remove_ack_after_reply"),
            response_prefix=m.get("response_prefix"),
            suppress_tool_errors=m.get("suppress_tool_errors"),
            after_tool_results_prompt=m.get("after_tool_results_prompt"),
            group_chat=m.get("group_chat"),
        )
    if update.commands and isinstance(update.commands, dict):
        from joyhousebot.config.schema import CommandsConfig
        c = update.commands
        config.commands = CommandsConfig(
            native=c.get("native", "auto"),
            native_skills=c.get("native_skills", "auto"),
        )
    if update.env and isinstance(update.env, dict):
        from joyhousebot.config.schema import EnvConfig
        config.env = EnvConfig(vars=dict(update.env.get("vars") or {}))

