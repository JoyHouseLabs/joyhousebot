"""Singleton manager for bridge + native plugin lifecycle and calls."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from loguru import logger

from .bridge.manager import BridgePluginManager
from .core.contracts import BridgeRuntime, NativeRuntime
from .core.types import PluginHostError, PluginRecord, PluginSnapshot
from .native.loader import NativePluginLoader, NativeRegistry

_singleton_lock = threading.Lock()
_singleton: "PluginManager | None" = None
_NATIVE_CIRCUIT_THRESHOLD = 3
_NATIVE_CIRCUIT_COOLDOWN_SECONDS = 30.0


class PluginManager:
    """Coordinates bridge host loading, native plugins, and dispatch."""

    def __init__(self, openclaw_dir: str | None = None):
        self.bridge: BridgeRuntime = BridgePluginManager(openclaw_dir=openclaw_dir)
        # Keep `client` for backward compatibility (`plugins setup-host` uses it).
        self.client = self.bridge.client
        self.native: NativeRuntime = NativePluginLoader()
        self.native_registry: NativeRegistry | None = None
        self.snapshot: PluginSnapshot | None = None
        self._lock = threading.RLock()
        self._last_workspace_dir: str | None = None
        self._last_config: dict[str, Any] = {}
        self._runtime_stats_path: Path | None = None
        self._native_circuit: dict[str, dict[str, Any]] = {}
        self._native_runtime_stats: dict[str, Any] = {
            "startedAtMs": int(time.time() * 1000),
            "totals": {"calls": 0, "ok": 0, "errors": 0, "timeouts": 0},
            "byKind": {
                "rpc": {"calls": 0, "ok": 0, "errors": 0, "timeouts": 0},
                "http": {"calls": 0, "ok": 0, "errors": 0, "timeouts": 0},
                "cli": {"calls": 0, "ok": 0, "errors": 0, "timeouts": 0},
            },
            "recentErrors": [],
        }

    def load(self, workspace_dir: str, config: dict[str, Any], reload: bool = False) -> PluginSnapshot:
        with self._lock:
            if reload or self.snapshot is None:
                self._last_workspace_dir = workspace_dir
                self._last_config = config
                self._runtime_stats_path = Path(workspace_dir) / ".joyhouse" / "plugin-runtime-stats.json"
                self._load_runtime_stats()
                bridge_snapshot: PluginSnapshot | None = None
                bridge_error: Exception | None = None
                try:
                    bridge_snapshot = self.bridge.load(workspace_dir=workspace_dir, config=config, reload=reload)
                except Exception as exc:
                    bridge_error = exc
                self.native_registry = self.native.load(workspace_dir=workspace_dir, config=config)
                if bridge_snapshot is None and bridge_error and not self.native_registry.records:
                    logger.warning("Bridge plugin load failed: {}", bridge_error)
                if bridge_snapshot is None and bridge_error and not self.native_registry.records:
                    if isinstance(bridge_error, PluginHostError):
                        raise bridge_error
                    raise PluginHostError("BRIDGE_LOAD_FAILED", str(bridge_error))
                self.snapshot = self._merge_snapshots(workspace_dir, bridge_snapshot, self.native_registry)
            return self.snapshot

    def status(self) -> PluginSnapshot:
        with self._lock:
            bridge_snapshot: PluginSnapshot | None = None
            try:
                bridge_snapshot = self.bridge.status()
            except Exception:
                bridge_snapshot = None
            if self._last_workspace_dir:
                self.native_registry = self.native.load(
                    workspace_dir=self._last_workspace_dir,
                    config=self._last_config,
                )
            self.snapshot = self._merge_snapshots(self._last_workspace_dir or "", bridge_snapshot, self.native_registry)
            return self.snapshot

    def list_plugins(self) -> list[dict[str, Any]]:
        snapshot = self.snapshot or self.status()
        return [asdict(row) for row in snapshot.plugins]

    def info(self, plugin_id: str) -> dict[str, Any]:
        pid = plugin_id.strip()
        snapshot = self.snapshot or self.status()
        for row in snapshot.plugins:
            if row.id == pid or row.name == pid:
                return asdict(row)
        return {}

    def doctor(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        try:
            out["bridge"] = self.client.doctor()
        except Exception as exc:
            out["bridge"] = {"ok": False, "error": str(exc)}
        workspace_dir = self._last_workspace_dir or str(Path.cwd())
        native_report = self.native.doctor(workspace_dir=workspace_dir, config=self._last_config)
        native_report["runtime"] = self.native_runtime_report()
        out["native"] = native_report
        return out

    def native_doctor(self, workspace_dir: str, config: dict[str, Any]) -> dict[str, Any]:
        report = self.native.doctor(workspace_dir=workspace_dir, config=config)
        report["runtime"] = self.native_runtime_report()
        return report

    def gateway_methods(self) -> list[str]:
        snapshot = self.snapshot or self.status()
        return list(dict.fromkeys(snapshot.gateway_methods))

    def invoke_gateway_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        registry = self.native_registry
        if registry and method in registry.rpc_handlers:
            return self._invoke_native_guard(
                kind="rpc",
                key=method,
                invoker=lambda: self.native.invoke_rpc(registry, method=method, params=params or {}),
            )
        return self.client.invoke_gateway_method(method=method, params=params)

    def http_dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        registry = self.native_registry
        path = str(request.get("path") or "").strip()
        if registry is not None and path in registry.http_handlers:
            method = str(request.get("method") or "GET").upper()
            return self._invoke_native_guard(
                kind="http",
                key=f"{method} {path}",
                invoker=lambda: self.native.dispatch_http(registry, request),
            )
        try:
            return self.client.http_dispatch(request)
        except Exception as exc:
            return {
                "ok": False,
                "error": {"code": "UNAVAILABLE", "message": f"plugin http dispatch unavailable: {exc}"},
            }

    def skills_dirs(self) -> list[str]:
        if self.snapshot is not None and self.snapshot.skills_dirs:
            return self.snapshot.skills_dirs
        registry = self.native_registry
        native_dirs = registry.skills_dirs if registry else []
        bridge_dirs = self.client.skills_dirs()
        return list(dict.fromkeys([*bridge_dirs, *native_dirs]))

    def channels_list(self) -> list[str]:
        bridge_rows = self.client.channels_list()
        native_rows = self.native_registry.channel_ids if self.native_registry else []
        return list(dict.fromkeys([*bridge_rows, *native_rows]))

    def providers_list(self) -> list[str]:
        bridge_rows = self.client.providers_list()
        native_rows = self.native_registry.provider_ids if self.native_registry else []
        return list(dict.fromkeys([*bridge_rows, *native_rows]))

    def hooks_list(self) -> list[dict[str, Any]]:
        bridge_hooks = self.client.hooks_list()
        native_hooks = self.native_registry.hooks if self.native_registry else []
        return [*bridge_hooks, *native_hooks]

    def cli_commands(self) -> list[str]:
        snapshot = self.snapshot or self.status()
        commands: list[str] = []
        for row in snapshot.plugins:
            commands.extend([str(x) for x in row.cli_commands])
        return list(dict.fromkeys(commands))

    def invoke_cli_command(self, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        registry = self.native_registry
        if registry is not None and command in registry.cli_handlers:
            return self._invoke_native_guard(
                kind="cli",
                key=command,
                invoker=lambda: self.native.invoke_cli(registry, command=command, payload=payload or {}),
            )
        return {
            "ok": False,
            "error": {
                "code": "UNAVAILABLE",
                "message": f"cli command not available in native runtime: {command}",
            },
        }

    def invoke_plugin_tool(
        self, plugin_id: str, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Invoke a plugin tool by plugin_id and tool_name. Native first, then bridge fallback."""
        pid = str(plugin_id or "").strip()
        tname = str(tool_name or "").strip()
        if not pid:
            return {"ok": False, "error": {"code": "INVALID_REQUEST", "message": "plugin_id required"}}
        if not tname:
            return {"ok": False, "error": {"code": "INVALID_REQUEST", "message": "tool_name required"}}
        resolved_name = tname if "." in tname else f"{pid}.{tname}"
        args = dict(arguments or {})

        registry = self.native_registry
        if registry is not None:
            for record in registry.records:
                if record.id == pid and resolved_name in (record.tool_names or []):
                    if resolved_name in registry.tool_handlers:

                        def _invoke() -> dict[str, Any]:
                            out = self.native.invoke_tool(registry, resolved_name, args)
                            if isinstance(out, dict) and out.get("ok") and "plugin_id" not in out:
                                out = {"ok": True, "plugin_id": pid, "tool_name": resolved_name, "result": out.get("result")}
                            return out

                        return self._invoke_native_guard(
                            kind="tool",
                            key=resolved_name,
                            invoker=_invoke,
                        )
                    break

        try:
            out = self.client.invoke_tool(name=resolved_name, args=args)
            if isinstance(out, dict) and out.get("ok") is False and "error" in out:
                return {"ok": False, "error": {"code": "BRIDGE_TOOL_ERROR", "message": str(out.get("error", ""))}}
            return {"ok": True, "plugin_id": pid, "tool_name": resolved_name, "result": out}
        except Exception as exc:
            return {"ok": False, "error": {"code": "UNAVAILABLE", "message": f"plugin tool unavailable: {exc}"}}

    def native_runtime_report(self) -> dict[str, Any]:
        totals = dict(self._native_runtime_stats.get("totals", {}))
        by_kind = self._native_runtime_stats.get("byKind", {})
        recent_errors = self._native_runtime_stats.get("recentErrors", [])
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - (24 * 60 * 60 * 1000)
        recent_24h = [
            row for row in recent_errors if int(row.get("tsMs", 0) or 0) >= cutoff_ms
        ]
        errors_by_code: dict[str, int] = {}
        for row in recent_24h:
            code = str(row.get("code") or "UNKNOWN")
            errors_by_code[code] = int(errors_by_code.get(code, 0)) + 1
        calls = int(totals.get("calls", 0) or 0)
        circuit_hits = int(errors_by_code.get("NATIVE_CIRCUIT_OPEN", 0))
        circuit_hit_rate = (circuit_hits / calls) if calls > 0 else 0.0
        open_circuits = []
        now = time.time()
        for key, state in self._native_circuit.items():
            open_until = float(state.get("open_until", 0.0) or 0.0)
            if open_until > now:
                open_circuits.append(
                    {
                        "key": key,
                        "failureStreak": int(state.get("failure_streak", 0) or 0),
                        "retryAfterSeconds": max(0.0, round(open_until - now, 3)),
                    }
                )
        return {
            "startedAtMs": int(self._native_runtime_stats.get("startedAtMs", 0) or 0),
            "totals": totals,
            "byKind": by_kind,
            "openCircuits": open_circuits,
            "recentErrors": recent_errors,
            "last24h": {
                "errorsByCode": errors_by_code,
                "errorCount": len(recent_24h),
                "circuitOpenHits": circuit_hits,
                "circuitHitRate": round(circuit_hit_rate, 6),
            },
        }

    def status_report(self) -> dict[str, Any]:
        snapshot = self.snapshot or self.status()
        by_origin: dict[str, int] = {}
        for row in snapshot.plugins:
            origin = str(row.origin or "unknown")
            by_origin[origin] = int(by_origin.get(origin, 0) + 1)
        errored = len([p for p in snapshot.plugins if p.status == "error"])
        loaded = len([p for p in snapshot.plugins if p.status == "loaded"])
        runtime = self.native_runtime_report()
        return {
            "ok": True,
            "workspaceDir": snapshot.workspace_dir,
            "openclawDir": snapshot.openclaw_dir,
            "plugins": {
                "total": len(snapshot.plugins),
                "loaded": loaded,
                "errored": errored,
                "byOrigin": by_origin,
            },
            "gatewayMethods": len(snapshot.gateway_methods),
            "tools": len(snapshot.tool_names),
            "services": len(snapshot.service_ids),
            "channels": len(snapshot.channel_ids),
            "providers": len(snapshot.provider_ids),
            "hooks": len(snapshot.hook_names),
            "skillsDirs": len(snapshot.skills_dirs),
            "nativeRuntime": runtime,
            "tsMs": int(time.time() * 1000),
        }

    def start_services(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            rows.extend(self.client.start_services())
        except Exception as exc:
            rows.append({"id": "bridge", "started": False, "error": str(exc), "runtime": "bridge"})
        registry = self.native_registry
        if registry is None:
            return rows
        for service_id, meta in registry.services.items():
            if bool(meta.get("started")):
                rows.append({"id": service_id, "started": True, "error": "", "runtime": "native"})
                continue
            try:
                start = meta.get("start")
                if callable(start):
                    start()
                meta["started"] = True
                rows.append({"id": service_id, "started": True, "error": "", "runtime": "native"})
            except Exception as exc:
                rows.append({"id": service_id, "started": False, "error": str(exc), "runtime": "native"})
        for channel_id, meta in registry.channels.items():
            if bool(meta.get("started")):
                rows.append({"id": channel_id, "started": True, "error": "", "runtime": "native", "type": "channel"})
                continue
            try:
                start = meta.get("start")
                if callable(start):
                    start()
                meta["started"] = True
                rows.append({"id": channel_id, "started": True, "error": "", "runtime": "native", "type": "channel"})
            except Exception as exc:
                rows.append({"id": channel_id, "started": False, "error": str(exc), "runtime": "native", "type": "channel"})
        return rows

    def stop_services(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        registry = self.native_registry
        if registry is not None:
            for channel_id, meta in registry.channels.items():
                if not bool(meta.get("started")):
                    rows.append({"id": channel_id, "stopped": True, "error": "", "runtime": "native", "type": "channel"})
                    continue
                try:
                    stop = meta.get("stop")
                    if callable(stop):
                        stop()
                    meta["started"] = False
                    rows.append({"id": channel_id, "stopped": True, "error": "", "runtime": "native", "type": "channel"})
                except Exception as exc:
                    rows.append({"id": channel_id, "stopped": False, "error": str(exc), "runtime": "native", "type": "channel"})
            for service_id, meta in registry.services.items():
                if not bool(meta.get("started")):
                    rows.append({"id": service_id, "stopped": True, "error": "", "runtime": "native"})
                    continue
                try:
                    stop = meta.get("stop")
                    if callable(stop):
                        stop()
                    meta["started"] = False
                    rows.append({"id": service_id, "stopped": True, "error": "", "runtime": "native"})
                except Exception as exc:
                    rows.append({"id": service_id, "stopped": False, "error": str(exc), "runtime": "native"})
        try:
            rows.extend(self.client.stop_services())
        except Exception as exc:
            rows.append({"id": "bridge", "stopped": False, "error": str(exc), "runtime": "bridge"})
        return rows

    def close(self) -> None:
        try:
            self.stop_services()
        except Exception:
            pass
        self.client.close()

    def _merge_snapshots(
        self,
        workspace_dir: str,
        bridge_snapshot: PluginSnapshot | None,
        native_registry: NativeRegistry | None,
    ) -> PluginSnapshot:
        native_registry = native_registry or NativeRegistry(loaded_at_ms=int(time.time() * 1000))
        bridge_plugins = bridge_snapshot.plugins if bridge_snapshot else []
        bridge_diagnostics = bridge_snapshot.diagnostics if bridge_snapshot else []
        bridge_gateway_methods = bridge_snapshot.gateway_methods if bridge_snapshot else []
        bridge_tool_names = bridge_snapshot.tool_names if bridge_snapshot else []
        bridge_service_ids = bridge_snapshot.service_ids if bridge_snapshot else []
        bridge_channel_ids = bridge_snapshot.channel_ids if bridge_snapshot else []
        bridge_provider_ids = bridge_snapshot.provider_ids if bridge_snapshot else []
        bridge_hook_names = bridge_snapshot.hook_names if bridge_snapshot else []
        bridge_skills = bridge_snapshot.skills_dirs if bridge_snapshot else []
        return PluginSnapshot(
            loaded_at_ms=max(
                bridge_snapshot.loaded_at_ms if bridge_snapshot else 0,
                native_registry.loaded_at_ms or 0,
            ),
            workspace_dir=workspace_dir,
            openclaw_dir=bridge_snapshot.openclaw_dir if bridge_snapshot else "",
            plugins=[*bridge_plugins, *native_registry.records],
            diagnostics=[*bridge_diagnostics, *native_registry.diagnostics],
            gateway_methods=list(dict.fromkeys([*bridge_gateway_methods, *native_registry.gateway_methods])),
            tool_names=list(dict.fromkeys([*bridge_tool_names, *native_registry.tool_names])),
            service_ids=list(dict.fromkeys([*bridge_service_ids, *native_registry.service_ids])),
            channel_ids=list(dict.fromkeys([*bridge_channel_ids, *native_registry.channel_ids])),
            provider_ids=list(dict.fromkeys([*bridge_provider_ids, *native_registry.provider_ids])),
            hook_names=list(dict.fromkeys([*bridge_hook_names, *native_registry.hook_names])),
            skills_dirs=list(dict.fromkeys([*bridge_skills, *native_registry.skills_dirs])),
        )

    def _invoke_native_guard(
        self,
        *,
        kind: str,
        key: str,
        invoker: Any,
    ) -> dict[str, Any]:
        circuit_key = f"{kind}:{key}"
        now = time.time()
        state = self._native_circuit.setdefault(circuit_key, {"failure_streak": 0, "open_until": 0.0})
        open_until = float(state.get("open_until", 0.0) or 0.0)
        if open_until > now:
            self._record_native_call(
                kind=kind,
                ok=False,
                error_code="NATIVE_CIRCUIT_OPEN",
                error_message=f"native circuit open for {circuit_key}",
                target=key,
            )
            return {
                "ok": False,
                "error": {
                    "code": "NATIVE_CIRCUIT_OPEN",
                    "message": f"native circuit open for {circuit_key}",
                    "data": {"retryAfterSeconds": max(0.0, round(open_until - now, 3))},
                },
            }
        result = invoker()
        error_obj = result.get("error") if isinstance(result, dict) else None
        if bool(result.get("ok")):
            state["failure_streak"] = 0
            state["open_until"] = 0.0
            self._record_native_call(kind=kind, ok=True, error_code="", error_message="", target=key)
            return result
        error_code = str(error_obj.get("code") if isinstance(error_obj, dict) else "NATIVE_ERROR")
        error_message = str(error_obj.get("message") if isinstance(error_obj, dict) else "native call failed")
        state["failure_streak"] = int(state.get("failure_streak", 0) or 0) + 1
        if state["failure_streak"] >= _NATIVE_CIRCUIT_THRESHOLD:
            state["open_until"] = time.time() + _NATIVE_CIRCUIT_COOLDOWN_SECONDS
        self._record_native_call(
            kind=kind,
            ok=False,
            error_code=error_code,
            error_message=error_message,
            target=key,
        )
        return result

    def _record_native_call(
        self,
        *,
        kind: str,
        ok: bool,
        error_code: str,
        error_message: str,
        target: str,
    ) -> None:
        totals = self._native_runtime_stats.setdefault("totals", {})
        totals["calls"] = int(totals.get("calls", 0) or 0) + 1
        by_kind = self._native_runtime_stats.setdefault("byKind", {})
        kind_row = by_kind.setdefault(kind, {"calls": 0, "ok": 0, "errors": 0, "timeouts": 0})
        kind_row["calls"] = int(kind_row.get("calls", 0) or 0) + 1
        if ok:
            totals["ok"] = int(totals.get("ok", 0) or 0) + 1
            kind_row["ok"] = int(kind_row.get("ok", 0) or 0) + 1
            self._save_runtime_stats()
            return
        totals["errors"] = int(totals.get("errors", 0) or 0) + 1
        kind_row["errors"] = int(kind_row.get("errors", 0) or 0) + 1
        if "TIMEOUT" in error_code.upper():
            totals["timeouts"] = int(totals.get("timeouts", 0) or 0) + 1
            kind_row["timeouts"] = int(kind_row.get("timeouts", 0) or 0) + 1
        recent = self._native_runtime_stats.setdefault("recentErrors", [])
        recent.insert(
            0,
            {
                "tsMs": int(time.time() * 1000),
                "kind": kind,
                "target": target,
                "code": error_code,
                "message": error_message,
            },
        )
        self._native_runtime_stats["recentErrors"] = recent[:100]
        self._save_runtime_stats()

    def _load_runtime_stats(self) -> None:
        path = self._runtime_stats_path
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        totals = payload.get("totals")
        by_kind = payload.get("byKind")
        recent_errors = payload.get("recentErrors")
        started_at = payload.get("startedAtMs")
        if isinstance(started_at, int):
            self._native_runtime_stats["startedAtMs"] = started_at
        if isinstance(totals, dict):
            self._native_runtime_stats["totals"] = {
                "calls": int(totals.get("calls", 0) or 0),
                "ok": int(totals.get("ok", 0) or 0),
                "errors": int(totals.get("errors", 0) or 0),
                "timeouts": int(totals.get("timeouts", 0) or 0),
            }
        if isinstance(by_kind, dict):
            merged: dict[str, dict[str, int]] = {}
            for kind in ("rpc", "http", "cli", "tool"):
                row = by_kind.get(kind, {})
                if not isinstance(row, dict):
                    row = {}
                merged[kind] = {
                    "calls": int(row.get("calls", 0) or 0),
                    "ok": int(row.get("ok", 0) or 0),
                    "errors": int(row.get("errors", 0) or 0),
                    "timeouts": int(row.get("timeouts", 0) or 0),
                }
            self._native_runtime_stats["byKind"] = merged
        if isinstance(recent_errors, list):
            normalized: list[dict[str, Any]] = []
            for row in recent_errors[:100]:
                if not isinstance(row, dict):
                    continue
                normalized.append(
                    {
                        "tsMs": int(row.get("tsMs", 0) or 0),
                        "kind": str(row.get("kind") or ""),
                        "target": str(row.get("target") or ""),
                        "code": str(row.get("code") or ""),
                        "message": str(row.get("message") or ""),
                    }
                )
            self._native_runtime_stats["recentErrors"] = normalized

    def _save_runtime_stats(self) -> None:
        path = self._runtime_stats_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "startedAtMs": int(self._native_runtime_stats.get("startedAtMs", 0) or 0),
                "totals": self._native_runtime_stats.get("totals", {}),
                "byKind": self._native_runtime_stats.get("byKind", {}),
                "recentErrors": self._native_runtime_stats.get("recentErrors", []),
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return


def get_plugin_manager(openclaw_dir: str | None = None) -> PluginManager:
    """Get or create process-global plugin manager."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = PluginManager(openclaw_dir=openclaw_dir)
    return _singleton


def initialize_plugins_for_workspace(workspace: Path, config: Any, force_reload: bool = False) -> PluginSnapshot | None:
    """Best-effort plugin host load for gateway and CLI."""
    manager = get_plugin_manager()
    try:
        config_payload = (
            config.model_dump(by_alias=True)
            if hasattr(config, "model_dump")
            else (config if isinstance(config, dict) else {})
        )
        snapshot = manager.load(workspace_dir=str(workspace), config=config_payload, reload=force_reload)
        return snapshot
    except PluginHostError as exc:
        logger.warning("Plugin host unavailable: {}", exc)
        return None
    except Exception as exc:
        logger.warning("Failed to initialize plugins: {}", exc)
        return None

