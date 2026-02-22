"""Minimal python-native plugin loader (tools/rpc/hooks)."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from joyhousebot.plugins.core.types import PluginRecord
from joyhousebot.plugins.discovery import MANIFEST_FILENAME, get_plugin_roots

DEFAULT_NATIVE_CALL_TIMEOUT_SECONDS = 15.0


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _enabled_capabilities(manifest: dict[str, Any]) -> list[str]:
    caps = _safe_dict(manifest.get("capabilities"))
    out: list[str] = []
    for key in ("tools", "rpc", "hooks", "services", "cli", "channels", "providers", "http"):
        if bool(caps.get(key)):
            out.append(key)
    return out


def _plugins_config(config: dict[str, Any]) -> dict[str, Any]:
    return _safe_dict(config.get("plugins"))


def _plugin_enabled(plugin_id: str, plugins_cfg: dict[str, Any]) -> bool:
    if not bool(plugins_cfg.get("enabled", True)):
        return False
    deny = set(plugins_cfg.get("deny") or [])
    if plugin_id in deny:
        return False
    allow = set(plugins_cfg.get("allow") or [])
    if allow and plugin_id not in allow:
        return False
    entries = _safe_dict(plugins_cfg.get("entries"))
    entry = _safe_dict(entries.get(plugin_id))
    if "enabled" in entry:
        return bool(entry.get("enabled"))
    return True


@dataclass(slots=True)
class NativeRegistry:
    """Loaded native plugin registry."""

    records: list[PluginRecord] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    skills_dirs: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    gateway_methods: list[str] = field(default_factory=list)
    hook_names: list[str] = field(default_factory=list)
    service_ids: list[str] = field(default_factory=list)
    http_paths: list[str] = field(default_factory=list)
    cli_commands: list[str] = field(default_factory=list)
    provider_ids: list[str] = field(default_factory=list)
    channel_ids: list[str] = field(default_factory=list)
    rpc_handlers: dict[str, Callable[[dict[str, Any]], Any]] = field(default_factory=dict)
    tool_handlers: dict[str, Callable[..., Any]] = field(default_factory=dict)
    hooks: list[dict[str, Any]] = field(default_factory=list)
    services: dict[str, dict[str, Any]] = field(default_factory=dict)
    http_handlers: dict[str, dict[str, Any]] = field(default_factory=dict)
    cli_handlers: dict[str, dict[str, Any]] = field(default_factory=dict)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    loaded_at_ms: int = 0


class _NativeApi:
    def __init__(self, plugin_id: str, plugin_config: dict[str, Any]):
        self.plugin_id = plugin_id
        self.plugin_config = plugin_config
        self.tools: dict[str, Callable[..., Any]] = {}
        self.rpc: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self.hooks: list[dict[str, Any]] = []
        self.services: dict[str, dict[str, Any]] = {}
        self.http: dict[str, dict[str, Any]] = {}
        self.cli: dict[str, dict[str, Any]] = {}
        self.providers: dict[str, dict[str, Any]] = {}
        self.channels: dict[str, dict[str, Any]] = {}

    def register_tool(
        self,
        tool: dict[str, Any] | str,
        handler: Callable[..., Any] | None = None,
    ) -> None:
        if isinstance(tool, dict):
            name = str(tool.get("name") or "").strip()
            fn = tool.get("handler")
            if not callable(fn):
                raise ValueError("tool.handler must be callable")
            self.tools[name] = fn
            return
        name = str(tool).strip()
        if not name:
            raise ValueError("tool name is required")
        if not callable(handler):
            raise ValueError("tool handler must be callable")
        self.tools[name] = handler

    def register_rpc(self, method: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        method_name = str(method).strip()
        if not method_name:
            raise ValueError("rpc method name is required")
        if not callable(handler):
            raise ValueError("rpc handler must be callable")
        self.rpc[method_name] = handler

    def register_hook(self, hook_name: str, handler: Callable[..., Any], priority: int = 0) -> None:
        name = str(hook_name).strip()
        if not name:
            raise ValueError("hook name is required")
        if not callable(handler):
            raise ValueError("hook handler must be callable")
        self.hooks.append({"hookName": name, "handler": handler, "priority": int(priority)})

    def register_service(
        self,
        service: dict[str, Any] | str,
        *,
        start: Callable[[], Any] | None = None,
        stop: Callable[[], Any] | None = None,
    ) -> None:
        if isinstance(service, dict):
            service_id = str(service.get("id") or "").strip()
            if not service_id:
                raise ValueError("service id is required")
            self.services[service_id] = {
                "start": service.get("start"),
                "stop": service.get("stop"),
                "started": False,
            }
            return
        service_id = str(service).strip()
        if not service_id:
            raise ValueError("service id is required")
        self.services[service_id] = {"start": start, "stop": stop, "started": False}

    def register_http(
        self,
        path: str,
        handler: Callable[[dict[str, Any]], Any],
        *,
        methods: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        route = str(path).strip()
        if not route:
            raise ValueError("http path is required")
        if not route.startswith("/"):
            route = f"/{route}"
        if not callable(handler):
            raise ValueError("http handler must be callable")
        allowed = [str(m).upper() for m in (methods or ["GET", "POST"]) if str(m).strip()]
        self.http[route] = {"handler": handler, "methods": allowed}

    def register_cli(
        self,
        command: str,
        handler: Callable[[dict[str, Any]], Any],
        *,
        description: str | None = None,
    ) -> None:
        command_name = str(command).strip()
        if not command_name:
            raise ValueError("cli command is required")
        if not callable(handler):
            raise ValueError("cli handler must be callable")
        self.cli[command_name] = {"handler": handler, "description": str(description or "").strip()}

    def register_provider(
        self,
        provider: dict[str, Any] | str,
        handler: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        if isinstance(provider, dict):
            provider_id = str(provider.get("id") or "").strip()
            if not provider_id:
                raise ValueError("provider id is required")
            candidate = provider.get("handler")
            self.providers[provider_id] = {
                "handler": candidate if callable(candidate) else None,
                "meta": provider,
            }
            return
        provider_id = str(provider).strip()
        if not provider_id:
            raise ValueError("provider id is required")
        self.providers[provider_id] = {"handler": handler if callable(handler) else None, "meta": {"id": provider_id}}

    def register_channel(
        self,
        channel: dict[str, Any] | str,
        *,
        start: Callable[[], Any] | None = None,
        stop: Callable[[], Any] | None = None,
    ) -> None:
        if isinstance(channel, dict):
            channel_id = str(channel.get("id") or "").strip()
            if not channel_id:
                raise ValueError("channel id is required")
            self.channels[channel_id] = {
                "meta": channel,
                "start": channel.get("start"),
                "stop": channel.get("stop"),
                "started": False,
            }
            return
        channel_id = str(channel).strip()
        if not channel_id:
            raise ValueError("channel id is required")
        self.channels[channel_id] = {
            "meta": {"id": channel_id},
            "start": start,
            "stop": stop,
            "started": False,
        }


class NativePluginLoader:
    """Loads python-native plugins from manifest + entrypoint."""

    def discover(self, workspace_dir: str, config: dict[str, Any]) -> list[tuple[Path, dict[str, Any]]]:
        roots = get_plugin_roots(workspace_dir, config)
        manifests: list[tuple[Path, dict[str, Any]]] = []
        for root in roots:
            try:
                parsed = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(parsed, dict):
                manifests.append((root, parsed))
        return manifests

    def _load_entry(self, plugin_root: Path, entry: str) -> Any:
        module_ref, _, obj_name = entry.partition(":")
        object_name = obj_name.strip() or "plugin"
        module_ref = module_ref.strip()
        if not module_ref:
            raise ValueError("entry module is required")
        if module_ref.endswith(".py"):
            module_path = (plugin_root / module_ref).resolve()
            if not module_path.exists():
                raise FileNotFoundError(f"entry file not found: {module_path}")
            module_name = f"joyhouse_native_{plugin_root.name}_{abs(hash(str(module_path)))}"
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"failed to load spec for {module_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        else:
            sys.path.insert(0, str(plugin_root))
            try:
                module = importlib.import_module(module_ref)
            finally:
                try:
                    sys.path.remove(str(plugin_root))
                except ValueError:
                    pass
        if not hasattr(module, object_name):
            raise AttributeError(f"entry object not found: {object_name}")
        target = getattr(module, object_name)
        if inspect.isclass(target):
            return target()
        return target

    def load(self, workspace_dir: str, config: dict[str, Any]) -> NativeRegistry:
        plugins_cfg = _plugins_config(config)
        registry = NativeRegistry(loaded_at_ms=int(time.time() * 1000))
        seen_tools: dict[str, str] = {}
        seen_rpc: dict[str, str] = {}
        seen_http: dict[str, str] = {}
        seen_cli: dict[str, str] = {}
        seen_services: dict[str, str] = {}
        seen_providers: dict[str, str] = {}
        seen_channels: dict[str, str] = {}
        for root, manifest in self.discover(workspace_dir=workspace_dir, config=config):
            plugin_id = str(manifest.get("id") or "").strip()
            runtime = str(manifest.get("runtime") or "python-native").strip()
            capabilities = _enabled_capabilities(manifest)
            if not plugin_id:
                registry.diagnostics.append(
                    {"level": "error", "pluginId": "-", "message": f"missing id in {root / MANIFEST_FILENAME}"}
                )
                continue
            enabled = _plugin_enabled(plugin_id, plugins_cfg)
            if not enabled:
                continue
            if runtime != "python-native":
                continue
            entry = str(manifest.get("entry") or "plugin.py:plugin").strip()
            entries = _safe_dict(plugins_cfg.get("entries"))
            plugin_cfg = _safe_dict(_safe_dict(entries.get(plugin_id)).get("config"))
            try:
                plugin_obj = self._load_entry(root, entry)
                api = _NativeApi(plugin_id=plugin_id, plugin_config=plugin_cfg)
                if hasattr(plugin_obj, "register") and callable(getattr(plugin_obj, "register")):
                    plugin_obj.register(api)
                elif callable(plugin_obj):
                    plugin_obj(api)
                else:
                    raise TypeError("entry object must expose register(api) or be callable")
                accepted_tools: list[str] = []
                for tool_name, tool_handler in api.tools.items():
                    owner = seen_tools.get(tool_name)
                    if owner and owner != plugin_id:
                        registry.diagnostics.append(
                            {
                                "level": "warning",
                                "code": "NATIVE_CONFLICT_TOOL",
                                "pluginId": plugin_id,
                                "message": f"tool name conflict: {tool_name} already owned by {owner}",
                            }
                        )
                        continue
                    seen_tools[tool_name] = plugin_id
                    accepted_tools.append(tool_name)
                    registry.tool_names.append(tool_name)
                    registry.tool_handlers[tool_name] = tool_handler

                accepted_rpc: dict[str, Callable[[dict[str, Any]], Any]] = {}
                for method_name, method_handler in api.rpc.items():
                    owner = seen_rpc.get(method_name)
                    if owner and owner != plugin_id:
                        registry.diagnostics.append(
                            {
                                "level": "warning",
                                "code": "NATIVE_CONFLICT_RPC",
                                "pluginId": plugin_id,
                                "message": f"rpc method conflict: {method_name} already owned by {owner}",
                            }
                        )
                        continue
                    seen_rpc[method_name] = plugin_id
                    accepted_rpc[method_name] = method_handler
                    registry.gateway_methods.append(method_name)
                registry.rpc_handlers.update(accepted_rpc)

                registry.hook_names.extend(str(h.get("hookName") or "") for h in api.hooks if h.get("hookName"))
                accepted_services: list[str] = []
                accepted_http: list[str] = []
                accepted_cli: list[str] = []
                accepted_providers: list[str] = []
                accepted_channels: list[str] = []

                for service_id, service_meta in api.services.items():
                    owner = seen_services.get(service_id)
                    if owner and owner != plugin_id:
                        registry.diagnostics.append(
                            {
                                "level": "warning",
                                "code": "NATIVE_CONFLICT_SERVICE",
                                "pluginId": plugin_id,
                                "message": f"service id conflict: {service_id} already owned by {owner}",
                            }
                        )
                        continue
                    seen_services[service_id] = plugin_id
                    accepted_services.append(service_id)
                    registry.service_ids.append(service_id)
                    registry.services[service_id] = {
                        "pluginId": plugin_id,
                        "start": service_meta.get("start"),
                        "stop": service_meta.get("stop"),
                        "started": bool(service_meta.get("started", False)),
                    }

                for http_path, http_meta in api.http.items():
                    owner = seen_http.get(http_path)
                    if owner and owner != plugin_id:
                        registry.diagnostics.append(
                            {
                                "level": "warning",
                                "code": "NATIVE_CONFLICT_HTTP",
                                "pluginId": plugin_id,
                                "message": f"http path conflict: {http_path} already owned by {owner}",
                            }
                        )
                        continue
                    seen_http[http_path] = plugin_id
                    accepted_http.append(http_path)
                    registry.http_paths.append(http_path)
                    registry.http_handlers[http_path] = {
                        "pluginId": plugin_id,
                        "handler": http_meta.get("handler"),
                        "methods": list(http_meta.get("methods") or []),
                    }

                for command_name, command_meta in api.cli.items():
                    owner = seen_cli.get(command_name)
                    if owner and owner != plugin_id:
                        registry.diagnostics.append(
                            {
                                "level": "warning",
                                "code": "NATIVE_CONFLICT_CLI",
                                "pluginId": plugin_id,
                                "message": f"cli command conflict: {command_name} already owned by {owner}",
                            }
                        )
                        continue
                    seen_cli[command_name] = plugin_id
                    accepted_cli.append(command_name)
                    registry.cli_commands.append(command_name)
                    registry.cli_handlers[command_name] = {
                        "pluginId": plugin_id,
                        "handler": command_meta.get("handler"),
                        "description": str(command_meta.get("description") or ""),
                    }

                for provider_id, provider_meta in api.providers.items():
                    owner = seen_providers.get(provider_id)
                    if owner and owner != plugin_id:
                        registry.diagnostics.append(
                            {
                                "level": "warning",
                                "code": "NATIVE_CONFLICT_PROVIDER",
                                "pluginId": plugin_id,
                                "message": f"provider id conflict: {provider_id} already owned by {owner}",
                            }
                        )
                        continue
                    seen_providers[provider_id] = plugin_id
                    accepted_providers.append(provider_id)
                    registry.provider_ids.append(provider_id)
                    registry.providers[provider_id] = {
                        "pluginId": plugin_id,
                        "handler": provider_meta.get("handler"),
                        "meta": provider_meta.get("meta") if isinstance(provider_meta.get("meta"), dict) else {},
                    }

                for channel_id, channel_meta in api.channels.items():
                    owner = seen_channels.get(channel_id)
                    if owner and owner != plugin_id:
                        registry.diagnostics.append(
                            {
                                "level": "warning",
                                "code": "NATIVE_CONFLICT_CHANNEL",
                                "pluginId": plugin_id,
                                "message": f"channel id conflict: {channel_id} already owned by {owner}",
                            }
                        )
                        continue
                    seen_channels[channel_id] = plugin_id
                    accepted_channels.append(channel_id)
                    registry.channel_ids.append(channel_id)
                    registry.channels[channel_id] = {
                        "pluginId": plugin_id,
                        "meta": channel_meta.get("meta") if isinstance(channel_meta.get("meta"), dict) else {},
                        "start": channel_meta.get("start"),
                        "stop": channel_meta.get("stop"),
                        "started": bool(channel_meta.get("started", False)),
                    }
                for h in api.hooks:
                    registry.hooks.append(
                        {
                            "pluginId": plugin_id,
                            "hookName": str(h.get("hookName") or ""),
                            "priority": int(h.get("priority") or 0),
                            "runtime": "native",
                        }
                    )
                for raw_skill_dir in manifest.get("skills") or []:
                    if not isinstance(raw_skill_dir, str) or not raw_skill_dir.strip():
                        continue
                    skill_dir = (root / raw_skill_dir).resolve()
                    if skill_dir.exists():
                        registry.skills_dirs.append(str(skill_dir))
                registry.records.append(
                    PluginRecord(
                        id=plugin_id,
                        name=str(manifest.get("name") or plugin_id),
                        source=str(root),
                        origin="native",
                        status="loaded",
                        enabled=True,
                        version=str(manifest.get("version") or "") or None,
                        description=str(manifest.get("description") or "") or None,
                        kind=str(manifest.get("kind") or runtime) or None,
                        runtime=runtime,
                        capabilities=capabilities,
                        tool_names=accepted_tools,
                        hook_names=[str(h.get("hookName") or "") for h in api.hooks if h.get("hookName")],
                        channel_ids=accepted_channels,
                        provider_ids=accepted_providers,
                        cli_commands=accepted_cli,
                        services=accepted_services,
                        commands=[*accepted_http, *accepted_cli],
                        gateway_methods=list(accepted_rpc.keys()),
                    )
                )
            except Exception as exc:
                registry.records.append(
                    PluginRecord(
                        id=plugin_id,
                        name=str(manifest.get("name") or plugin_id),
                        source=str(root),
                        origin="native",
                        status="error",
                        enabled=True,
                        version=str(manifest.get("version") or "") or None,
                        description=str(manifest.get("description") or "") or None,
                        kind=str(manifest.get("kind") or runtime) or None,
                        runtime=runtime,
                        capabilities=capabilities,
                        error=str(exc),
                    )
                )
                registry.diagnostics.append({"level": "error", "pluginId": plugin_id, "message": str(exc)})

        registry.gateway_methods = list(dict.fromkeys(registry.gateway_methods))
        registry.tool_names = list(dict.fromkeys(registry.tool_names))
        registry.hook_names = list(dict.fromkeys(registry.hook_names))
        registry.service_ids = list(dict.fromkeys(registry.service_ids))
        registry.http_paths = list(dict.fromkeys(registry.http_paths))
        registry.cli_commands = list(dict.fromkeys(registry.cli_commands))
        registry.provider_ids = list(dict.fromkeys(registry.provider_ids))
        registry.channel_ids = list(dict.fromkeys(registry.channel_ids))
        registry.skills_dirs = list(dict.fromkeys(registry.skills_dirs))
        return registry

    def doctor(self, workspace_dir: str, config: dict[str, Any]) -> dict[str, Any]:
        discovered = self.discover(workspace_dir=workspace_dir, config=config)
        registry = self.load(workspace_dir=workspace_dir, config=config)
        return {
            "checks": {
                "workspaceDirExists": Path(workspace_dir).expanduser().exists(),
                "discoveredCount": len(discovered),
                "loadedCount": len([r for r in registry.records if r.status == "loaded"]),
                "errorCount": len([r for r in registry.records if r.status == "error"]),
            },
            "plugins": [
                {
                    "id": r.id,
                    "status": r.status,
                    "origin": r.origin,
                    "source": r.source,
                    "error": r.error,
                }
                for r in registry.records
            ],
            "httpPaths": registry.http_paths,
            "cliCommands": registry.cli_commands,
            "providerIds": registry.provider_ids,
            "channelIds": registry.channel_ids,
            "diagnostics": registry.diagnostics,
        }

    def dispatch_http(self, registry: NativeRegistry, request: dict[str, Any]) -> dict[str, Any]:
        method = str(request.get("method") or "GET").upper()
        path = str(request.get("path") or "").strip()
        if not path:
            return {"ok": False, "error": {"code": "INVALID_REQUEST", "message": "path required"}}
        route = registry.http_handlers.get(path)
        if route is None:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": f"native http route not found: {path}"}}
        allowed = [str(x).upper() for x in route.get("methods", [])]
        if allowed and method not in allowed:
            return {
                "ok": False,
                "error": {"code": "METHOD_NOT_ALLOWED", "message": f"method {method} not allowed for {path}"},
            }
        handler = route.get("handler")
        if not callable(handler):
            return {"ok": False, "error": {"code": "NATIVE_INVALID_HANDLER", "message": "http handler is not callable"}}
        try:
            payload = self._call_with_timeout(handler, request)
            if isinstance(payload, dict) and any(k in payload for k in ("status", "headers", "body")):
                return {
                    "ok": True,
                    "status": int(payload.get("status", 200)),
                    "headers": payload.get("headers", {}) if isinstance(payload.get("headers"), dict) else {},
                    "body": payload.get("body"),
                }
            return {"ok": True, "status": 200, "headers": {}, "body": payload}
        except TimeoutError as exc:
            return {"ok": False, "error": {"code": "NATIVE_HTTP_TIMEOUT", "message": str(exc)}}
        except Exception as exc:
            return {"ok": False, "error": {"code": "NATIVE_HTTP_ERROR", "message": str(exc)}}

    def invoke_cli(self, registry: NativeRegistry, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        command_name = str(command or "").strip()
        if not command_name:
            return {"ok": False, "error": {"code": "INVALID_REQUEST", "message": "command required"}}
        entry = registry.cli_handlers.get(command_name)
        if entry is None:
            return {"ok": False, "error": {"code": "NATIVE_NOT_FOUND", "message": f"native cli command not found: {command_name}"}}
        handler = entry.get("handler")
        if not callable(handler):
            return {"ok": False, "error": {"code": "NATIVE_INVALID_HANDLER", "message": "cli handler is not callable"}}
        try:
            result = self._call_with_timeout(handler, payload or {})
            return {"ok": True, "result": result}
        except TimeoutError as exc:
            return {"ok": False, "error": {"code": "NATIVE_CLI_TIMEOUT", "message": str(exc)}}
        except Exception as exc:
            return {"ok": False, "error": {"code": "NATIVE_CLI_ERROR", "message": str(exc)}}

    def invoke_rpc(self, registry: NativeRegistry, method: str, params: dict[str, Any]) -> dict[str, Any]:
        method_name = str(method or "").strip()
        if not method_name:
            return {"ok": False, "error": {"code": "INVALID_REQUEST", "message": "rpc method required"}}
        handler = registry.rpc_handlers.get(method_name)
        if not callable(handler):
            return {"ok": False, "error": {"code": "NATIVE_NOT_FOUND", "message": f"rpc method not found: {method_name}"}}
        try:
            payload = self._call_with_timeout(handler, params or {})
            return {"ok": True, "payload": payload}
        except TimeoutError as exc:
            return {"ok": False, "error": {"code": "NATIVE_RPC_TIMEOUT", "message": str(exc)}}
        except Exception as exc:
            return {"ok": False, "error": {"code": "NATIVE_RPC_ERROR", "message": str(exc)}}

    def invoke_tool(
        self, registry: NativeRegistry, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        name = str(tool_name or "").strip()
        if not name:
            return {"ok": False, "error": {"code": "INVALID_REQUEST", "message": "tool name required"}}
        handler = registry.tool_handlers.get(name)
        if not callable(handler):
            return {"ok": False, "error": {"code": "TOOL_NOT_FOUND", "message": f"native tool not found: {name}"}}
        payload = dict(arguments or {})

        def _run(_ignored: dict[str, Any]) -> Any:
            try:
                return handler(**payload)
            except TypeError:
                return handler(payload)

        try:
            result = self._call_with_timeout(_run, {})
            return {"ok": True, "result": result}
        except TimeoutError as exc:
            return {"ok": False, "error": {"code": "NATIVE_TOOL_TIMEOUT", "message": str(exc)}}
        except Exception as exc:
            return {"ok": False, "error": {"code": "NATIVE_TOOL_ERROR", "message": str(exc)}}

    def _call_with_timeout(self, handler: Callable[[dict[str, Any]], Any], payload: dict[str, Any]) -> Any:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(handler, payload)
            try:
                return future.result(timeout=DEFAULT_NATIVE_CALL_TIMEOUT_SECONDS)
            except FutureTimeoutError as exc:
                raise TimeoutError(f"native handler timeout after {DEFAULT_NATIVE_CALL_TIMEOUT_SECONDS}s") from exc

