"""Simplified plugin manager for native Python plugins only."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from loguru import logger

from .core.types import PluginRecord, PluginSnapshot
from .native.loader import NativePluginLoader, NativeRegistry
from joyhousebot.utils.exceptions import sanitize_error_message

_singleton_lock = threading.Lock()
_singleton: "PluginManager | None" = None
_NATIVE_CIRCUIT_THRESHOLD = 3
_NATIVE_CIRCUIT_COOLDOWN_SECONDS = 30.0


class PluginManager:
    """Manages native Python plugins."""

    def __init__(self) -> None:
        self.native: NativePluginLoader = NativePluginLoader()
        self.registry: NativeRegistry | None = None
        self._lock = threading.RLock()
        self._last_workspace_dir: str | None = None
        self._last_config: dict[str, Any] = {}
        self._runtime_stats_path: Path | None = None
        self._circuit: dict[str, dict[str, Any]] = {}
        self._runtime_stats: dict[str, Any] = {
            "startedAtMs": int(time.time() * 1000),
            "totals": {"calls": 0, "ok": 0, "errors": 0, "timeouts": 0},
            "byKind": {
                "rpc": {"calls": 0, "ok": 0, "errors": 0, "timeouts": 0},
                "http": {"calls": 0, "ok": 0, "errors": 0, "timeouts": 0},
                "cli": {"calls": 0, "ok": 0, "errors": 0, "timeouts": 0},
                "tool": {"calls": 0, "ok": 0, "errors": 0, "timeouts": 0},
            },
            "recentErrors": [],
        }

    def load(self, workspace_dir: str, config: dict[str, Any], reload: bool = False) -> PluginSnapshot:
        with self._lock:
            if reload or self.registry is None:
                self._last_workspace_dir = workspace_dir
                self._last_config = config
                self._runtime_stats_path = Path(workspace_dir) / ".joyhouse" / "plugin-runtime-stats.json"
                self._load_runtime_stats()
                self.registry = self.native.load(workspace_dir=workspace_dir, config=config)
                logger.info(f"Loaded {len(self.registry.records)} native plugins")
            return self._build_snapshot(workspace_dir)

    def _build_snapshot(self, workspace_dir: str) -> PluginSnapshot:
        registry = self.registry or NativeRegistry(loaded_at_ms=int(time.time() * 1000))
        return PluginSnapshot(
            loaded_at_ms=registry.loaded_at_ms or int(time.time() * 1000),
            workspace_dir=workspace_dir,
            plugins=registry.records,
            diagnostics=registry.diagnostics,
            gateway_methods=list(dict.fromkeys(registry.gateway_methods)),
            tool_names=list(dict.fromkeys(registry.tool_names)),
            service_ids=list(dict.fromkeys(registry.service_ids)),
            channel_ids=list(dict.fromkeys(registry.channel_ids)),
            provider_ids=list(dict.fromkeys(registry.provider_ids)),
            hook_names=list(dict.fromkeys(registry.hook_names)),
            skills_dirs=list(dict.fromkeys(registry.skills_dirs)),
        )

    @property
    def snapshot(self) -> PluginSnapshot | None:
        if self._last_workspace_dir and self.registry:
            return self._build_snapshot(self._last_workspace_dir)
        return None

    def list_plugins(self) -> list[dict[str, Any]]:
        if self.registry is None:
            return []
        return [asdict(row) for row in self.registry.records]

    def info(self, plugin_id: str) -> dict[str, Any]:
        pid = plugin_id.strip()
        if self.registry is None:
            return {}
        for row in self.registry.records:
            if row.id == pid or row.name == pid:
                return asdict(row)
        return {}

    def doctor(self) -> dict[str, Any]:
        workspace_dir = self._last_workspace_dir or str(Path.cwd())
        report = self.native.doctor(workspace_dir=workspace_dir, config=self._last_config)
        report["runtime"] = self.runtime_report()
        return report

    def gateway_methods(self) -> list[str]:
        if self.registry is None:
            return []
        return list(dict.fromkeys(self.registry.gateway_methods))

    def invoke_gateway_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.registry is None or method not in self.registry.rpc_handlers:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": f"Method not found: {method}"}}
        return self._invoke_guard(
            kind="rpc",
            key=method,
            invoker=lambda: self.native.invoke_rpc(self.registry, method=method, params=params or {}),
        )

    def invoke_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if self.registry is None or name not in self.registry.tool_handlers:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": f"Tool not found: {name}"}}
        return self._invoke_guard(
            kind="tool",
            key=name,
            invoker=lambda: self.native.invoke_tool(self.registry, name, args or {}),
        )

    def invoke_plugin_tool(self, plugin_id: str, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        pid = str(plugin_id or "").strip()
        tname = str(tool_name or "").strip()
        if not pid:
            return {"ok": False, "error": {"code": "INVALID_REQUEST", "message": "plugin_id required"}}
        if not tname:
            return {"ok": False, "error": {"code": "INVALID_REQUEST", "message": "tool_name required"}}
        resolved_name = tname if "." in tname else f"{pid}.{tname}"
        args = dict(arguments or {})

        if self.registry is not None:
            for record in self.registry.records:
                if record.id == pid and resolved_name in (record.tool_names or []):
                    if resolved_name in self.registry.tool_handlers:
                        def _invoke() -> dict[str, Any]:
                            out = self.native.invoke_tool(self.registry, resolved_name, args)
                            if isinstance(out, dict) and out.get("ok") and "plugin_id" not in out:
                                out = {"ok": True, "plugin_id": pid, "tool_name": resolved_name, "result": out.get("result")}
                            return out
                        return self._invoke_guard(kind="tool", key=resolved_name, invoker=_invoke)
                    break
        return {"ok": False, "error": {"code": "NOT_FOUND", "message": f"Tool not found: {resolved_name}"}}

    def http_dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.registry is None:
            return {"ok": False, "error": {"code": "NOT_READY", "message": "Plugins not loaded"}}
        path = str(request.get("path") or "").strip()
        if path not in self.registry.http_handlers:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": f"HTTP path not found: {path}"}}
        method = str(request.get("method") or "GET").upper()
        return self._invoke_guard(
            kind="http",
            key=f"{method} {path}",
            invoker=lambda: self.native.dispatch_http(self.registry, request),
        )

    def skills_dirs(self) -> list[str]:
        if self.registry is None:
            return []
        return self.registry.skills_dirs

    def channels_list(self) -> list[str]:
        if self.registry is None:
            return []
        return self.registry.channel_ids

    def providers_list(self) -> list[str]:
        if self.registry is None:
            return []
        return self.registry.provider_ids

    def hooks_list(self) -> list[dict[str, Any]]:
        if self.registry is None:
            return []
        return self.registry.hooks

    def cli_commands(self) -> list[str]:
        if self.registry is None:
            return []
        return self.registry.cli_commands

    def invoke_cli_command(self, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.registry is None or command not in self.registry.cli_handlers:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": f"CLI command not found: {command}"}}
        return self._invoke_guard(
            kind="cli",
            key=command,
            invoker=lambda: self.native.invoke_cli(self.registry, command=command, payload=payload or {}),
        )

    def runtime_report(self) -> dict[str, Any]:
        totals = dict(self._runtime_stats.get("totals", {}))
        by_kind = self._runtime_stats.get("byKind", {})
        recent_errors = self._runtime_stats.get("recentErrors", [])
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - (24 * 60 * 60 * 1000)
        recent_24h = [row for row in recent_errors if int(row.get("tsMs", 0) or 0) >= cutoff_ms]
        errors_by_code: dict[str, int] = {}
        for row in recent_24h:
            code = str(row.get("code") or "UNKNOWN")
            errors_by_code[code] = int(errors_by_code.get(code, 0) + 1)
        calls = int(totals.get("calls", 0) or 0)
        circuit_hits = int(errors_by_code.get("NATIVE_CIRCUIT_OPEN", 0))
        circuit_hit_rate = (circuit_hits / calls) if calls > 0 else 0.0
        open_circuits = []
        now = time.time()
        for key, state in self._circuit.items():
            open_until = float(state.get("open_until", 0.0) or 0.0)
            if open_until > now:
                open_circuits.append({
                    "key": key,
                    "failureStreak": int(state.get("failure_streak", 0) or 0),
                    "retryAfterSeconds": max(0.0, round(open_until - now, 3)),
                })
        return {
            "startedAtMs": int(self._runtime_stats.get("startedAtMs", 0) or 0),
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
        snapshot = self.snapshot
        if snapshot is None:
            return {"ok": False, "error": "Plugins not loaded"}
        by_origin: dict[str, int] = {}
        for row in snapshot.plugins:
            origin = str(row.origin or "unknown")
            by_origin[origin] = int(by_origin.get(origin, 0) + 1)
        errored = len([p for p in snapshot.plugins if p.status == "error"])
        loaded = len([p for p in snapshot.plugins if p.status == "loaded"])
        return {
            "ok": True,
            "workspaceDir": snapshot.workspace_dir,
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
            "nativeRuntime": self.runtime_report(),
            "tsMs": int(time.time() * 1000),
        }

    def start_services(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if self.registry is None:
            return rows
        for service_id, meta in self.registry.services.items():
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
        for channel_id, meta in self.registry.channels.items():
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
        if self.registry is None:
            return rows
        for channel_id, meta in self.registry.channels.items():
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
        for service_id, meta in self.registry.services.items():
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
        return rows

    def close(self) -> None:
        try:
            self.stop_services()
        except Exception:
            pass

    def _invoke_guard(self, *, kind: str, key: str, invoker: Any) -> dict[str, Any]:
        circuit_key = f"{kind}:{key}"
        now = time.time()
        state = self._circuit.setdefault(circuit_key, {"failure_streak": 0, "open_until": 0.0})
        open_until = float(state.get("open_until", 0.0) or 0.0)
        if open_until > now:
            self._record_call(kind=kind, ok=False, error_code="CIRCUIT_OPEN", error_message=f"Circuit open for {circuit_key}", target=key)
            return {
                "ok": False,
                "error": {
                    "code": "CIRCUIT_OPEN",
                    "message": f"Circuit open for {circuit_key}",
                    "data": {"retryAfterSeconds": max(0.0, round(open_until - now, 3))},
                },
            }
        result = invoker()
        error_obj = result.get("error") if isinstance(result, dict) else None
        if bool(result.get("ok")):
            state["failure_streak"] = 0
            state["open_until"] = 0.0
            self._record_call(kind=kind, ok=True, error_code="", error_message="", target=key)
            return result
        error_code = str(error_obj.get("code") if isinstance(error_obj, dict) else "ERROR")
        error_message = str(error_obj.get("message") if isinstance(error_obj, dict) else "Call failed")
        state["failure_streak"] = int(state.get("failure_streak", 0) or 0) + 1
        if state["failure_streak"] >= _NATIVE_CIRCUIT_THRESHOLD:
            state["open_until"] = time.time() + _NATIVE_CIRCUIT_COOLDOWN_SECONDS
        self._record_call(kind=kind, ok=False, error_code=error_code, error_message=error_message, target=key)
        return result

    def _record_call(self, *, kind: str, ok: bool, error_code: str, error_message: str, target: str) -> None:
        totals = self._runtime_stats.setdefault("totals", {})
        totals["calls"] = int(totals.get("calls", 0) or 0) + 1
        by_kind = self._runtime_stats.setdefault("byKind", {})
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
        recent = self._runtime_stats.setdefault("recentErrors", [])
        recent.insert(0, {"tsMs": int(time.time() * 1000), "kind": kind, "target": target, "code": error_code, "message": error_message})
        self._runtime_stats["recentErrors"] = recent[:100]
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
            self._runtime_stats["startedAtMs"] = started_at
        if isinstance(totals, dict):
            self._runtime_stats["totals"] = {
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
            self._runtime_stats["byKind"] = merged
        if isinstance(recent_errors, list):
            normalized: list[dict[str, Any]] = []
            for row in recent_errors[:100]:
                if not isinstance(row, dict):
                    continue
                normalized.append({
                    "tsMs": int(row.get("tsMs", 0) or 0),
                    "kind": str(row.get("kind") or ""),
                    "target": str(row.get("target") or ""),
                    "code": str(row.get("code") or ""),
                    "message": str(row.get("message") or ""),
                })
            self._runtime_stats["recentErrors"] = normalized

    def _save_runtime_stats(self) -> None:
        path = self._runtime_stats_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "startedAtMs": int(self._runtime_stats.get("startedAtMs", 0) or 0),
                "totals": self._runtime_stats.get("totals", {}),
                "byKind": self._runtime_stats.get("byKind", {}),
                "recentErrors": self._runtime_stats.get("recentErrors", []),
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def get_plugin_manager() -> PluginManager:
    """Get or create process-global plugin manager."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = PluginManager()
    return _singleton


def initialize_plugins_for_workspace(workspace: Path, config: Any, force_reload: bool = False) -> PluginSnapshot | None:
    """Load native plugins for the given workspace."""
    manager = get_plugin_manager()
    try:
        config_payload = (
            config.model_dump(by_alias=True)
            if hasattr(config, "model_dump")
            else (config if isinstance(config, dict) else {})
        )
        snapshot = manager.load(workspace_dir=str(workspace), config=config_payload, reload=force_reload)
        return snapshot
    except Exception as exc:
        logger.warning("Failed to initialize plugins: {}", sanitize_error_message(str(exc)))
        return None
