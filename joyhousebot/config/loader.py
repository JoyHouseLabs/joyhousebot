"""Configuration loading utilities."""

import json
import os
from pathlib import Path
from typing import Any

from joyhousebot.config.schema import Config

# OpenClaw models.providers provider id -> joyhousebot ProvidersConfig key (for migration)
OPENCLAW_TO_JOYHOUSE_PROVIDER: dict[str, str] = {
    "zai": "zhipu",
    "anthropic": "anthropic",
    "openai": "openai",
    "openrouter": "openrouter",
    "deepseek": "deepseek",
    "groq": "groq",
    "dashscope": "dashscope",
    "moonshot": "moonshot",
    "minimax": "minimax",
    "vllm": "vllm",
    "gemini": "gemini",
    "zhipu": "zhipu",
    "aihubmix": "aihubmix",
}


def get_config_path() -> Path:
    """Get the default configuration file path."""
    return Path.home() / ".joyhousebot" / "config.json"


def get_data_dir() -> Path:
    """Get the joyhousebot data directory."""
    from joyhousebot.utils.helpers import get_data_path
    return get_data_path()


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.
    
    Args:
        config_path: Optional path to config file. Uses default if not provided.
    
    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()
    
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            data = _migrate_config(data)
            cfg = Config.model_validate(convert_keys(data))
            _apply_config_env_vars(cfg)
            return cfg
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(
                f"Failed to load config from {path}: {e}. "
                "Fix the file or remove it to regenerate defaults."
            ) from e
    
    return Config()


def _apply_config_env_vars(cfg: Config) -> None:
    """Apply config.env.vars to os.environ with setdefault (do not overwrite existing)."""
    if not cfg.env or not cfg.env.vars:
        return
    for key, value in cfg.env.vars.items():
        if isinstance(key, str) and isinstance(value, str):
            os.environ.setdefault(key, value)


def _apply_openclaw_models_providers(data: dict[str, Any]) -> None:
    """
    Map OpenClaw models.providers (baseUrl, apiKey) into joyhousebot providers.
    Mutates data in place. Call after convert_keys so keys are snake_case.
    """
    models = data.get("models")
    if not isinstance(models, dict):
        return
    providers_src = models.get("providers")
    if not isinstance(providers_src, dict):
        return
    providers_dst = data.setdefault("providers", {})
    custom_set = False
    for openclaw_id, prov in providers_src.items():
        if not isinstance(prov, dict):
            continue
        base_url = prov.get("base_url") or prov.get("baseUrl")
        api_key = prov.get("api_key") or prov.get("apiKey")
        jb_name = OPENCLAW_TO_JOYHOUSE_PROVIDER.get((openclaw_id or "").strip().lower(), "custom")
        if jb_name == "custom":
            if custom_set:
                continue
            custom_set = True
        target = providers_dst.setdefault(jb_name, {})
        if isinstance(target, dict):
            if base_url and isinstance(base_url, str) and base_url.strip():
                target["api_base"] = base_url.strip()
            if api_key and isinstance(api_key, str) and api_key.strip():
                target["api_key"] = api_key.strip()


def load_config_from_openclaw_file(path: Path) -> Config:
    """
    Load joyhousebot Config from an OpenClaw-style config file (e.g. openclaw.json).
    Applies the same migration and key conversion as load_config.
    OpenClaw models.providers.*.baseUrl are mapped into joyhousebot providers (e.g. zai -> zhipu.api_base).
    Only top-level keys that joyhousebot Config supports are kept; OpenClaw-only keys (meta, wizard, etc.) are ignored.
    Only plain JSON is supported (no $include or JSON5).
    """
    if not path.exists():
        raise FileNotFoundError(f"OpenClaw config not found: {path}")
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Config file must be a JSON object: {path}")
    data = _migrate_config(data)
    data = convert_keys(data)
    _apply_openclaw_models_providers(data)
    # Keep only keys that Config accepts (OpenClaw has meta, wizard, models, etc. we don't have)
    allowed = set(Config.model_fields)
    data = {k: v for k, v in data.items() if k in allowed}
    cfg = Config.model_validate(data)
    _apply_config_env_vars(cfg)
    return cfg


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.
    
    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Dump with by_alias so agents.agent_list is serialized as "list" (OpenClaw compat)
    data = config.model_dump(by_alias=True)
    data = convert_to_camel(data)
    
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    # Keep facade cache coherent without introducing hard import cycles.
    try:
        from joyhousebot.config.access import clear_config_cache

        clear_config_cache(config_path=path)
    except Exception:
        pass


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    # OpenClaw-style model object support:
    # - agents.defaults.model: {primary, fallbacks}
    # - agents.list[].model: {primary, fallbacks}
    agents = data.get("agents", {})
    defaults = agents.get("defaults", {})
    model = defaults.get("model")
    if isinstance(model, dict):
        primary = model.get("primary")
        if isinstance(primary, str) and primary.strip():
            defaults["model"] = primary.strip()
        fallbacks = model.get("fallbacks")
        if isinstance(fallbacks, list) and "modelFallbacks" not in defaults:
            normalized = [str(x).strip() for x in fallbacks if str(x).strip()]
            defaults["modelFallbacks"] = normalized
            if not defaults.get("model") and normalized:
                defaults["model"] = normalized[0]
    for entry in agents.get("list", []) if isinstance(agents.get("list"), list) else []:
        emodel = entry.get("model")
        if isinstance(emodel, dict):
            primary = emodel.get("primary")
            if isinstance(primary, str) and primary.strip():
                entry["model"] = primary.strip()
            fallbacks = emodel.get("fallbacks")
            if isinstance(fallbacks, list) and "modelFallbacks" not in entry:
                normalized = [str(x).strip() for x in fallbacks if str(x).strip()]
                entry["modelFallbacks"] = normalized
                if not entry.get("model") and normalized:
                    entry["model"] = normalized[0]
    # OpenClaw gateway RPC settings compatibility.
    gateway = data.get("gateway", {})
    rpc = gateway.get("rpc")
    if isinstance(rpc, dict):
        if "enabled" in rpc and "rpcEnabled" not in gateway:
            gateway["rpcEnabled"] = bool(rpc.get("enabled"))
        canary = rpc.get("canaryMethods")
        if isinstance(canary, list) and "rpcCanaryMethods" not in gateway:
            gateway["rpcCanaryMethods"] = [str(x) for x in canary]
        if "shadowReads" in rpc and "rpcShadowReads" not in gateway:
            gateway["rpcShadowReads"] = bool(rpc.get("shadowReads"))
    auth = gateway.get("auth")
    if isinstance(auth, dict):
        scopes = auth.get("defaultScopes")
        if isinstance(scopes, list) and "rpcDefaultScopes" not in gateway:
            gateway["rpcDefaultScopes"] = [str(x) for x in scopes]
    nodes = gateway.get("nodes")
    if isinstance(nodes, dict):
        allow_commands = nodes.get("allowCommands")
        if isinstance(allow_commands, list) and "nodeAllowCommands" not in gateway:
            gateway["nodeAllowCommands"] = [str(x) for x in allow_commands]
        deny_commands = nodes.get("denyCommands")
        if isinstance(deny_commands, list) and "nodeDenyCommands" not in gateway:
            gateway["nodeDenyCommands"] = [str(x) for x in deny_commands]
        browser_cfg = nodes.get("browser")
        if isinstance(browser_cfg, dict):
            mode = browser_cfg.get("mode")
            if isinstance(mode, str) and mode.strip() and "nodeBrowserMode" not in gateway:
                gateway["nodeBrowserMode"] = mode.strip()
            target = browser_cfg.get("node")
            if isinstance(target, str) and target.strip() and "nodeBrowserTarget" not in gateway:
                gateway["nodeBrowserTarget"] = target.strip()
    control_plane = gateway.get("controlPlane")
    if isinstance(control_plane, dict):
        claim = control_plane.get("claimBackoff")
        if isinstance(claim, dict):
            retryable = claim.get("retryableSeconds")
            if (
                isinstance(retryable, (int, float))
                and "controlPlaneClaimRetryableBackoffSeconds" not in gateway
            ):
                gateway["controlPlaneClaimRetryableBackoffSeconds"] = float(retryable)
            non_retryable = claim.get("nonRetryableSeconds")
            if (
                isinstance(non_retryable, (int, float))
                and "controlPlaneClaimNonRetryableBackoffSeconds" not in gateway
            ):
                gateway["controlPlaneClaimNonRetryableBackoffSeconds"] = float(non_retryable)
        heartbeat = control_plane.get("heartbeatBackoff")
        if isinstance(heartbeat, dict):
            retryable = heartbeat.get("retryableSeconds")
            if (
                isinstance(retryable, (int, float))
                and "controlPlaneHeartbeatRetryableBackoffSeconds" not in gateway
            ):
                gateway["controlPlaneHeartbeatRetryableBackoffSeconds"] = float(retryable)
            non_retryable = heartbeat.get("nonRetryableSeconds")
            if (
                isinstance(non_retryable, (int, float))
                and "controlPlaneHeartbeatNonRetryableBackoffSeconds" not in gateway
            ):
                gateway["controlPlaneHeartbeatNonRetryableBackoffSeconds"] = float(non_retryable)

    # OpenClaw-style plugins compatibility.
    plugins = data.get("plugins")
    if isinstance(plugins, dict):
        if "loadPaths" in plugins and "load" not in plugins:
            paths = plugins.get("loadPaths")
            if isinstance(paths, list):
                plugins["load"] = {"paths": [str(x) for x in paths]}
        load_cfg = plugins.get("load")
        if isinstance(load_cfg, dict):
            paths = load_cfg.get("paths")
            if isinstance(paths, list):
                load_cfg["paths"] = [str(x) for x in paths]
        entries = plugins.get("entries")
        if isinstance(entries, dict):
            for key, value in list(entries.items()):
                if isinstance(value, dict):
                    if "enabled" not in value:
                        value["enabled"] = True
                    cfg_obj = value.get("config")
                    if cfg_obj is None or not isinstance(cfg_obj, dict):
                        value["config"] = {}
        installs = plugins.get("installs")
        if isinstance(installs, dict):
            for key, value in list(installs.items()):
                if isinstance(value, dict):
                    if "sourcePath" in value and "source_path" not in value:
                        value["source_path"] = value.get("sourcePath")
                    if "installPath" in value and "install_path" not in value:
                        value["install_path"] = value.get("installPath")
                    if "installedAt" in value and "installed_at" not in value:
                        value["installed_at"] = value.get("installedAt")

    # OpenClaw commands -> joyhousebot commands (native, nativeSkills).
    commands_src = data.get("commands")
    if isinstance(commands_src, dict):
        data["commands"] = commands_src
        if "native" not in data["commands"]:
            data["commands"]["native"] = "auto"
        if "nativeSkills" not in data["commands"]:
            data["commands"]["nativeSkills"] = "auto"
    # OpenClaw channels.telegram.commands.native -> joyhousebot channels.telegram.commandsNative (→ commands_native).
    channels = data.get("channels")
    if isinstance(channels, dict):
        telegram = channels.get("telegram")
        if isinstance(telegram, dict):
            cmd_overrides = telegram.get("commands")
            if isinstance(cmd_overrides, dict) and "native" in cmd_overrides:
                telegram["commandsNative"] = cmd_overrides.get("native")

    # OpenClaw env: vars + sugar (string keys under env except shellEnv/vars) -> joyhousebot env.vars (camelCase).
    env_src = data.get("env")
    if isinstance(env_src, dict):
        vars_merged: dict[str, str] = dict(env_src.get("vars") or {})
        for k, v in env_src.items():
            if k not in ("vars", "shellEnv") and isinstance(v, str):
                vars_merged[k] = v
        data["env"] = {"vars": vars_merged}

    # OpenClaw meta/wizard: normalize to camelCase so convert_keys produces snake_case for Config.
    meta_src = data.get("meta")
    if isinstance(meta_src, dict):
        data["meta"] = {
            "lastTouchedVersion": meta_src.get("lastTouchedVersion") or meta_src.get("last_touched_version"),
            "lastTouchedAt": meta_src.get("lastTouchedAt") or meta_src.get("last_touched_at"),
        }
    wizard_src = data.get("wizard")
    if isinstance(wizard_src, dict):
        data["wizard"] = {
            "lastRunAt": wizard_src.get("lastRunAt") or wizard_src.get("last_run_at"),
            "lastRunVersion": wizard_src.get("lastRunVersion") or wizard_src.get("last_run_version"),
            "lastRunCommand": wizard_src.get("lastRunCommand") or wizard_src.get("last_run_command"),
            "lastRunMode": wizard_src.get("lastRunMode") or wizard_src.get("last_run_mode"),
        }
    # OpenClaw browser block -> joyhousebot browser (local control service).
    browser_src = data.get("browser")
    if isinstance(browser_src, dict):
        browser_dst: dict[str, Any] = {}
        if "enabled" in browser_src:
            browser_dst["enabled"] = bool(browser_src.get("enabled", True))
        if "defaultProfile" in browser_src or "default_profile" in browser_src:
            browser_dst["defaultProfile"] = (
                str(browser_src.get("defaultProfile") or browser_src.get("default_profile") or "default").strip()
                or "default"
            )
        if "executablePath" in browser_src or "executable_path" in browser_src:
            raw = browser_src.get("executablePath") or browser_src.get("executable_path")
            if isinstance(raw, str) and raw.strip():
                browser_dst["executablePath"] = raw.strip()
        if "headless" in browser_src:
            browser_dst["headless"] = bool(browser_src.get("headless", False))
        profiles_src = browser_src.get("profiles")
        if isinstance(profiles_src, dict) and profiles_src:
            profiles_dst: dict[str, Any] = {}
            for name, p in profiles_src.items():
                if not isinstance(p, dict):
                    continue
                entry: dict[str, Any] = {}
                if "cdpPort" in p or "cdp_port" in p:
                    v = p.get("cdpPort") or p.get("cdp_port")
                    if isinstance(v, int):
                        entry["cdpPort"] = v
                if "cdpUrl" in p or "cdp_url" in p:
                    v = p.get("cdpUrl") or p.get("cdp_url")
                    if isinstance(v, str) and v.strip():
                        entry["cdpUrl"] = v.strip()
                if "color" in p and isinstance(p.get("color"), str):
                    entry["color"] = str(p["color"]).strip()
                if entry:
                    profiles_dst[str(name)] = entry
            if profiles_dst:
                browser_dst["profiles"] = profiles_dst
        if browser_dst:
            data["browser"] = browser_dst
    return data


def convert_keys(data: Any) -> Any:
    """Convert camelCase keys to snake_case for Pydantic.
    Keys under config.env.vars are preserved (they are env var names, e.g. API_KEY)."""
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for k, v in data.items():
            new_k = camel_to_snake(k)
            if new_k == "env" and isinstance(v, dict):
                # config.env has "vars"; skills.entries.*.env is a direct env-var dict — preserve keys (env var names).
                has_vars = any(camel_to_snake(ek) == "vars" for ek in v)
                if has_vars:
                    env_converted = {}
                    for ek, ev in v.items():
                        snake_ek = camel_to_snake(ek)
                        if snake_ek == "vars" and isinstance(ev, dict):
                            env_converted["vars"] = dict(ev)
                        else:
                            env_converted[snake_ek] = convert_keys(ev)
                    result["env"] = env_converted
                else:
                    result["env"] = dict(v)
            else:
                result[new_k] = convert_keys(v)
        return result
    if isinstance(data, list):
        return [convert_keys(item) for item in data]
    return data


def convert_to_camel(data: Any) -> Any:
    """Convert snake_case keys to camelCase.
    Keys under config.env.vars are preserved (env var names, e.g. API_KEY)."""
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for k, v in data.items():
            new_k = snake_to_camel(k)
            if new_k == "env" and isinstance(v, dict):
                has_vars = "vars" in v
                if has_vars:
                    env_converted = {}
                    for ek, ev in v.items():
                        camel_ek = snake_to_camel(ek)
                        if camel_ek == "vars" and isinstance(ev, dict):
                            env_converted["vars"] = dict(ev)
                        else:
                            env_converted[camel_ek] = convert_to_camel(ev)
                    result["env"] = env_converted
                else:
                    result["env"] = dict(v)
            else:
                result[new_k] = convert_to_camel(v)
        return result
    if isinstance(data, list):
        return [convert_to_camel(item) for item in data]
    return data


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    result = []
    for i, char in enumerate(name):
        if char.isupper() and i > 0:
            result.append("_")
        result.append(char.lower())
    return "".join(result)


def snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    components = name.split("_")
    return components[0] + "".join(x.title() for x in components[1:])
