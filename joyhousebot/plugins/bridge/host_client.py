"""RPC client for the Node plugin host sidecar (bridge runtime)."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.plugins.core.protocol import RpcRequest
from joyhousebot.plugins.core.serialization import (
    decode_response_payload,
    encode_request_line,
    safe_dict,
    to_plugin_host_error,
)
from joyhousebot.plugins.core.types import PluginHostError, PluginRecord, PluginSnapshot


class PluginHostClient:
    """Line-delimited JSON RPC client over stdio."""

    def __init__(self, openclaw_dir: str | None = None):
        self.openclaw_dir = (openclaw_dir or "").strip() or None
        self._proc: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._pending: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def _resolve_paths(self) -> tuple[Path, Path]:
        root = Path(__file__).resolve().parents[3]
        host_script = root / "plugin_host" / "src" / "host.mjs"
        openclaw_dir = (
            Path(self.openclaw_dir).expanduser().resolve()
            if self.openclaw_dir
            else (root.parent / "openclaw").resolve()
        )
        return host_script, openclaw_dir

    def requirements_report(self) -> dict[str, Any]:
        """Collect runtime requirements and readiness checks for plugin host."""
        host_script, openclaw_dir = self._resolve_paths()
        package_json = openclaw_dir / "package.json"
        dist_loader = openclaw_dir / "dist" / "plugins" / "loader.js"
        src_loader = openclaw_dir / "src" / "plugins" / "loader.ts"
        loader_available = dist_loader.exists() or src_loader.exists()
        node_bin = shutil.which("node")
        pnpm_bin = shutil.which("pnpm")
        npm_bin = shutil.which("npm")
        checks = {
            "hostScriptExists": host_script.exists(),
            "openclawDirExists": openclaw_dir.exists(),
            "openclawPackageJsonExists": package_json.exists(),
            "openclawDistLoaderExists": dist_loader.exists(),
            "openclawSrcLoaderExists": src_loader.exists(),
            "openclawLoaderAvailable": loader_available,
            "nodeAvailable": bool(node_bin),
            "pnpmAvailable": bool(pnpm_bin),
            "npmAvailable": bool(npm_bin),
        }
        suggestions: list[str] = []
        if not checks["nodeAvailable"]:
            suggestions.append("Install Node.js 22+ and ensure `node` is in PATH.")
        if not checks["openclawDirExists"]:
            suggestions.append(f"Ensure OpenClaw workspace exists at: {openclaw_dir}")
        if checks["openclawDirExists"] and not checks["openclawPackageJsonExists"]:
            suggestions.append(f"OpenClaw package.json is missing under: {openclaw_dir}")
        if checks["openclawDirExists"] and not loader_available:
            pkg_manager = "pnpm" if checks["pnpmAvailable"] else "npm"
            suggestions.append(f"OpenClaw loader missing. Need dist/plugins/loader.js or src/plugins/loader.ts. Try: cd {openclaw_dir} && {pkg_manager} run build")
        return {
            "paths": {
                "hostScript": str(host_script),
                "openclawDir": str(openclaw_dir),
                "openclawPackageJson": str(package_json),
                "openclawDistLoader": str(dist_loader),
                "openclawSrcLoader": str(src_loader),
            },
            "bins": {
                "node": node_bin or "",
                "pnpm": pnpm_bin or "",
                "npm": npm_bin or "",
            },
            "checks": checks,
            "suggestions": suggestions,
        }

    def setup_host(
        self,
        *,
        install_deps: bool = True,
        build_dist: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Prepare OpenClaw runtime dependencies and dist build."""
        report = self.requirements_report()
        checks = report["checks"]
        if not checks.get("nodeAvailable"):
            raise PluginHostError("NODE_NOT_FOUND", "node is not available in PATH", report)
        if not checks.get("openclawDirExists"):
            raise PluginHostError("OPENCLAW_DIR_NOT_FOUND", "openclaw workspace directory not found", report)
        if not checks.get("openclawPackageJsonExists"):
            raise PluginHostError("OPENCLAW_INVALID", "openclaw package.json missing", report)
        manager = "pnpm" if checks.get("pnpmAvailable") else "npm"
        if manager == "npm" and not checks.get("npmAvailable"):
            raise PluginHostError("PKG_MANAGER_NOT_FOUND", "neither pnpm nor npm is available", report)

        openclaw_dir = Path(report["paths"]["openclawDir"])
        planned: list[list[str]] = []
        if install_deps:
            planned.append([manager, "install", "--ignore-scripts"])
        if build_dist:
            planned.append([manager, "run", "build"])
        if dry_run:
            return {
                "ok": True,
                "dryRun": True,
                "manager": manager,
                "cwd": str(openclaw_dir),
                "planned": planned,
                "report": report,
            }

        executed: list[dict[str, Any]] = []
        for command in planned:
            try:
                proc = subprocess.run(
                    command,
                    cwd=str(openclaw_dir),
                    text=True,
                    capture_output=True,
                    check=True,
                )
                executed.append(
                    {
                        "command": command,
                        "ok": True,
                        "stdout": proc.stdout[-2000:],
                        "stderr": proc.stderr[-2000:],
                    }
                )
            except subprocess.CalledProcessError as exc:
                executed.append(
                    {
                        "command": command,
                        "ok": False,
                        "stdout": (exc.stdout or "")[-2000:],
                        "stderr": (exc.stderr or "")[-2000:],
                        "returncode": exc.returncode,
                    }
                )
                raise PluginHostError(
                    "SETUP_COMMAND_FAILED",
                    f"command failed: {' '.join(command)}",
                    {"executed": executed, "report": report},
                ) from exc
        return {
            "ok": True,
            "dryRun": False,
            "manager": manager,
            "cwd": str(openclaw_dir),
            "executed": executed,
            "report": self.requirements_report(),
        }

    def _spawn(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        host_script, openclaw_dir = self._resolve_paths()
        report = self.requirements_report()
        if not report["checks"]["nodeAvailable"]:
            raise PluginHostError("NODE_NOT_FOUND", "node is not available in PATH", report)
        if not report["checks"]["openclawDirExists"]:
            raise PluginHostError("OPENCLAW_DIR_NOT_FOUND", "openclaw workspace directory not found", report)
        if not report["checks"].get("openclawLoaderAvailable"):
            raise PluginHostError(
                "OPENCLAW_LOADER_MISSING",
                "need dist/plugins/loader.js or src/plugins/loader.ts in openclaw dir; run `pnpm run build` there or ensure src/plugins/loader.ts exists",
                report,
            )
        if not host_script.exists():
            raise PluginHostError("HOST_NOT_FOUND", f"plugin host script missing: {host_script}", report)
        plugin_host_dir = host_script.parent.parent
        use_tsx = (plugin_host_dir / "node_modules" / "tsx").exists()
        if use_tsx:
            command = ["node", "--import", "tsx", "./src/host.mjs"]
            spawn_cwd = str(plugin_host_dir)
        else:
            command = ["node", str(host_script)]
            spawn_cwd = str(openclaw_dir)
        env = os.environ.copy()
        env["JOYHOUSEBOT_OPENCLAW_DIR"] = str(openclaw_dir)
        env.setdefault("NODE_NO_WARNINGS", "1")
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=spawn_cwd,
            env=env,
            bufsize=1,
        )
        if not self._proc.stdout or not self._proc.stdin:
            raise PluginHostError("HOST_START_FAILED", "plugin host stdio is unavailable")
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        stderr_thread.start()

    def _stderr_loop(self) -> None:
        proc = self._proc
        if not proc or not proc.stderr:
            return
        for line in proc.stderr:
            text = line.strip()
            if text:
                logger.debug("[plugin-host] {}", text)

    def _reader_loop(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("Plugin host sent invalid JSON: {}", text[:200])
                continue
            req_id = payload.get("id")
            if not isinstance(req_id, str):
                continue
            with self._lock:
                waiter = self._pending.get(req_id)
            if waiter is not None:
                waiter.put(payload)

    def _rpc(self, method: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> Any:
        with self._lock:
            self._spawn()
            assert self._proc is not None
            assert self._proc.stdin is not None
            if self._proc.poll() is not None:
                raise PluginHostError("HOST_DIED", "plugin host process exited")
            req_id = uuid.uuid4().hex
            waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[req_id] = waiter
            frame = RpcRequest(id=req_id, method=method, params=params or {})
            self._proc.stdin.write(encode_request_line(frame) + "\n")
            self._proc.stdin.flush()
        try:
            payload = waiter.get(timeout=timeout)
        except queue.Empty as exc:
            raise PluginHostError("RPC_TIMEOUT", f"plugin host timeout for {method}") from exc
        finally:
            with self._lock:
                self._pending.pop(req_id, None)
        response = decode_response_payload(payload, fallback_id=req_id)
        if not response.ok:
            raise to_plugin_host_error(response, fallback_method=method)
        return response.result

    def health(self) -> dict[str, Any]:
        result = self._rpc("host.health")
        return safe_dict(result)

    def load(self, workspace_dir: str, config: dict[str, Any], openclaw_dir: str | None = None) -> PluginSnapshot:
        result = self._rpc(
            "plugins.load",
            {
                "workspaceDir": workspace_dir,
                "config": config,
                "openclawDir": openclaw_dir or self.openclaw_dir or "",
            },
            timeout=45.0,
        )
        return self._to_snapshot(result)

    def reload(self, workspace_dir: str, config: dict[str, Any], openclaw_dir: str | None = None) -> PluginSnapshot:
        result = self._rpc(
            "plugins.reload",
            {
                "workspaceDir": workspace_dir,
                "config": config,
                "openclawDir": openclaw_dir or self.openclaw_dir or "",
            },
            timeout=45.0,
        )
        return self._to_snapshot(result)

    def status(self) -> PluginSnapshot:
        return self._to_snapshot(self._rpc("plugins.status"))

    def list_plugins(self) -> list[dict[str, Any]]:
        result = self._rpc("plugins.list")
        return result if isinstance(result, list) else []

    def plugin_info(self, plugin_id: str) -> dict[str, Any]:
        result = self._rpc("plugins.info", {"id": plugin_id})
        return safe_dict(result)

    def doctor(self) -> dict[str, Any]:
        return safe_dict(self._rpc("plugins.doctor"))

    def gateway_methods(self) -> list[str]:
        result = self._rpc("plugins.gateway.methods")
        return [str(x) for x in result] if isinstance(result, list) else []

    def invoke_gateway_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        result = self._rpc("plugins.gateway.invoke", {"method": method, "params": params})
        return safe_dict(result)

    def tools_list(self) -> list[str]:
        result = self._rpc("plugins.tools.list")
        return [str(x) for x in result] if isinstance(result, list) else []

    def invoke_tool(
        self,
        name: str,
        args: Any,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self._rpc("plugins.tools.invoke", {"name": name, "args": args, "context": context or {}})
        return safe_dict(result)

    def start_services(self) -> list[dict[str, Any]]:
        result = self._rpc("plugins.services.start", timeout=45.0)
        return result if isinstance(result, list) else []

    def stop_services(self) -> list[dict[str, Any]]:
        result = self._rpc("plugins.services.stop", timeout=30.0)
        return result if isinstance(result, list) else []

    def skills_dirs(self) -> list[str]:
        result = self._rpc("plugins.skills.dirs")
        return [str(x) for x in result] if isinstance(result, list) else []

    def channels_list(self) -> list[str]:
        result = self._rpc("plugins.channels.list")
        return [str(x) for x in result] if isinstance(result, list) else []

    def providers_list(self) -> list[str]:
        result = self._rpc("plugins.providers.list")
        return [str(x) for x in result] if isinstance(result, list) else []

    def hooks_list(self) -> list[dict[str, Any]]:
        result = self._rpc("plugins.hooks.list")
        return result if isinstance(result, list) else []

    def http_dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        result = self._rpc("plugins.http.dispatch", {"request": request})
        return safe_dict(result)

    def close(self) -> None:
        proc = self._proc
        if not proc:
            return
        try:
            if proc.poll() is None:
                try:
                    self._rpc("plugins.shutdown", timeout=2.0)
                except Exception:
                    pass
                proc.terminate()
                proc.wait(timeout=2.0)
        except Exception:
            proc.kill()
        finally:
            self._proc = None

    def _to_snapshot(self, raw: Any) -> PluginSnapshot:
        data = safe_dict(raw)
        plugins_raw = data.get("plugins")
        records: list[PluginRecord] = []
        if isinstance(plugins_raw, list):
            for item in plugins_raw:
                row = safe_dict(item)
                records.append(
                    PluginRecord(
                        id=str(row.get("id") or ""),
                        name=str(row.get("name") or ""),
                        source=str(row.get("source") or ""),
                        origin=str(row.get("origin") or ""),
                        status=str(row.get("status") or ""),
                        enabled=bool(row.get("enabled", False)),
                        version=str(row.get("version")) if row.get("version") is not None else None,
                        description=str(row.get("description")) if row.get("description") is not None else None,
                        kind=str(row.get("kind")) if row.get("kind") is not None else None,
                        runtime=str(row.get("runtime")) if row.get("runtime") is not None else None,
                        capabilities=[str(x) for x in row.get("capabilities", [])]
                        if isinstance(row.get("capabilities"), list)
                        else [],
                        tool_names=[str(x) for x in row.get("toolNames", [])] if isinstance(row.get("toolNames"), list) else [],
                        hook_names=[str(x) for x in row.get("hookNames", [])] if isinstance(row.get("hookNames"), list) else [],
                        channel_ids=[str(x) for x in row.get("channelIds", [])] if isinstance(row.get("channelIds"), list) else [],
                        provider_ids=[str(x) for x in row.get("providerIds", [])] if isinstance(row.get("providerIds"), list) else [],
                        gateway_methods=[str(x) for x in row.get("gatewayMethods", [])] if isinstance(row.get("gatewayMethods"), list) else [],
                        cli_commands=[str(x) for x in row.get("cliCommands", [])] if isinstance(row.get("cliCommands"), list) else [],
                        services=[str(x) for x in row.get("services", [])] if isinstance(row.get("services"), list) else [],
                        commands=[str(x) for x in row.get("commands", [])] if isinstance(row.get("commands"), list) else [],
                        error=str(row.get("error")) if row.get("error") is not None else None,
                    )
                )
        return PluginSnapshot(
            loaded_at_ms=int(data.get("loadedAtMs") or 0),
            workspace_dir=str(data.get("workspaceDir") or ""),
            openclaw_dir=str(data.get("openclawDir") or ""),
            plugins=records,
            diagnostics=data.get("diagnostics") if isinstance(data.get("diagnostics"), list) else [],
            gateway_methods=[str(x) for x in data.get("gatewayMethods", [])]
            if isinstance(data.get("gatewayMethods"), list)
            else [],
            tool_names=[str(x) for x in data.get("toolNames", [])]
            if isinstance(data.get("toolNames"), list)
            else [],
            service_ids=[str(x) for x in data.get("serviceIds", [])]
            if isinstance(data.get("serviceIds"), list)
            else [],
            channel_ids=[str(x) for x in data.get("channelIds", [])]
            if isinstance(data.get("channelIds"), list)
            else [],
            provider_ids=[str(x) for x in data.get("providerIds", [])]
            if isinstance(data.get("providerIds"), list)
            else [],
            hook_names=[str(x) for x in data.get("hookNames", [])]
            if isinstance(data.get("hookNames"), list)
            else [],
            skills_dirs=[str(x) for x in data.get("skillsDirs", [])]
            if isinstance(data.get("skillsDirs"), list)
            else [],
        )

