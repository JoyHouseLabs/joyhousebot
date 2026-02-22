"""Sandbox/security/acp/dns services."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any

from joyhousebot.cli.services.protocol_service import ProtocolService
from joyhousebot.cli.services.state_service import StateService
from joyhousebot.config.loader import get_config_path, get_data_dir, load_config, save_config
from joyhousebot.sandbox.service import (
    explain_local,
    list_containers_local,
    recreate_containers_local,
)


class SandboxService:
    """Sandbox-related configuration service."""

    def __init__(self, state: StateService, protocol: ProtocolService | None = None):
        self.state = state
        self.protocol = protocol

    def status(self) -> dict:
        cfg = load_config()
        return {
            "restrict_to_workspace": bool(cfg.tools.restrict_to_workspace),
            "exec_timeout": int(cfg.tools.exec.timeout),
            "exec_shell_mode": bool(cfg.tools.exec.shell_mode),
            "custom_policy": self.state.read_json("sandbox_policy", {}),
        }

    def set(self, restrict_to_workspace: bool, timeout: int, shell_mode: bool) -> None:
        cfg = load_config()
        cfg.tools.restrict_to_workspace = restrict_to_workspace
        cfg.tools.exec.timeout = max(1, int(timeout))
        cfg.tools.exec.shell_mode = shell_mode
        save_config(cfg)

    def list(self, browser_only: bool = False) -> list[dict[str, Any]]:
        if self.protocol is not None:
            try:
                payload = self.protocol.call("sandbox.list", {"browser": bool(browser_only)})
                rows = payload.get("items") if isinstance(payload, dict) else payload
                if isinstance(rows, list):
                    return [x for x in rows if isinstance(x, dict)]
            except Exception:
                pass
        return list_containers_local(self.state.read_json, browser_only=browser_only)

    def recreate(
        self,
        all_items: bool,
        session: str,
        agent: str,
        browser_only: bool,
        force: bool,
    ) -> dict[str, Any]:
        if self.protocol is not None:
            try:
                return self.protocol.call(
                    "sandbox.recreate",
                    {
                        "all": bool(all_items),
                        "session": session.strip() or None,
                        "agent": agent.strip() or None,
                        "browser": bool(browser_only),
                        "force": bool(force),
                    },
                )
            except Exception:
                pass
        return recreate_containers_local(
            self.state.read_json,
            self.state.write_json,
            all_items=all_items,
            session=session,
            agent=agent,
            browser_only=browser_only,
            force=force,
        )

    def explain(self, session: str, agent: str) -> dict[str, Any]:
        if self.protocol is not None:
            try:
                return self.protocol.call(
                    "sandbox.explain",
                    {"session": session.strip() or None, "agent": agent.strip() or None},
                )
            except Exception:
                pass
        return explain_local(self.state.read_json, session=session, agent=agent)


class SecurityService:
    """Security scope/token service."""

    def __init__(self, protocol: ProtocolService):
        self.protocol = protocol

    def status(self) -> dict:
        cfg = load_config()
        return {
            "rpc_default_scopes": list(cfg.gateway.rpc_default_scopes),
            "node_allow_commands": list(cfg.gateway.node_allow_commands),
            "node_deny_commands": list(cfg.gateway.node_deny_commands),
            "sandbox": {
                "restrict_to_workspace": cfg.tools.restrict_to_workspace,
                "exec_timeout": cfg.tools.exec.timeout,
                "exec_shell_mode": cfg.tools.exec.shell_mode,
            },
        }

    def set_scopes(self, scopes_csv: str) -> list[str]:
        scope_list = [s.strip() for s in scopes_csv.split(",") if s.strip()]
        if not scope_list:
            raise ValueError("scopes cannot be empty")
        cfg = load_config()
        cfg.gateway.rpc_default_scopes = scope_list
        save_config(cfg)
        return scope_list

    def rotate_token(self, device_id: str, role: str, scopes_csv: str) -> dict:
        scopes = [x.strip() for x in scopes_csv.split(",") if x.strip()]
        return self.protocol.call(
            "device.token.rotate",
            {"deviceId": device_id or None, "role": role, "scopes": scopes},
        )

    def revoke_token(self, device_id: str) -> dict:
        return self.protocol.call("device.token.revoke", {"deviceId": device_id or None})

    def audit(self, deep: bool = False) -> dict[str, Any]:
        cfg = load_config()
        findings: list[dict[str, str]] = []

        if not cfg.gateway.rpc_default_scopes:
            findings.append(
                {
                    "severity": "warn",
                    "checkId": "gateway.rpc_default_scopes.empty",
                    "title": "Gateway default scopes empty",
                    "detail": "gateway.rpc_default_scopes is empty",
                    "remediation": "set minimal read/write scopes",
                }
            )
        if int(cfg.tools.exec.timeout) > 1800:
            findings.append(
                {
                    "severity": "warn",
                    "checkId": "tools.exec.timeout.high",
                    "title": "Exec timeout is too high",
                    "detail": "tools.exec.timeout is very high",
                    "remediation": "set timeout to <= 1800 seconds",
                }
            )
        if not bool(cfg.tools.restrict_to_workspace):
            findings.append(
                {
                    "severity": "warn",
                    "checkId": "tools.restrict_to_workspace.off",
                    "title": "Workspace restriction disabled",
                    "detail": "workspace restriction is disabled",
                    "remediation": "enable tools.restrict_to_workspace",
                }
            )
        container_enabled = getattr(cfg.tools.exec, "container_enabled", False)
        if bool(container_enabled):
            try:
                import asyncio
                from joyhousebot.sandbox.docker_backend import is_docker_available
                docker_ok = asyncio.run(is_docker_available())
            except Exception:
                docker_ok = False
            if not docker_ok:
                findings.append(
                    {
                        "severity": "warn",
                        "checkId": "sandbox.container_enabled_but_docker_unavailable",
                        "title": "Container isolation enabled but Docker unavailable",
                        "detail": "tools.exec.container_enabled is true but Docker is not available; execution will fall back to direct (host).",
                        "remediation": "Install/start Docker, or set container_enabled=false.",
                    }
                )

        cfg_path = get_config_path()
        try:
            mode = oct(cfg_path.stat().st_mode & 0o777)
        except Exception:
            mode = "unknown"
        if deep and mode not in {"0o600", "0o640"}:
            findings.append(
                {
                    "severity": "info",
                    "checkId": "config.permissions",
                    "title": "Config permission is not strict",
                    "detail": f"config permission is {mode}",
                    "remediation": "chmod 600 ~/.joyhousebot/config.json",
                }
            )

        summary = {"critical": 0, "warn": 0, "info": 0}
        for row in findings:
            sev = str(row.get("severity", "info"))
            if sev in summary:
                summary[sev] += 1
        return {"ts": int(time.time() * 1000), "summary": summary, "findings": findings, "deep": deep}

    def fix(self) -> dict[str, Any]:
        cfg = load_config()
        changes: list[str] = []
        if not cfg.gateway.rpc_default_scopes:
            cfg.gateway.rpc_default_scopes = ["operator.read", "operator.write"]
            changes.append("set gateway.rpc_default_scopes to operator.read/operator.write")
        if int(cfg.tools.exec.timeout) > 1800:
            cfg.tools.exec.timeout = 1800
            changes.append("clamped tools.exec.timeout to 1800")
        if not bool(cfg.tools.restrict_to_workspace):
            cfg.tools.restrict_to_workspace = True
            changes.append("enabled tools.restrict_to_workspace")
        if changes:
            save_config(cfg)
        return {"ok": True, "changes": changes}


class AcpService:
    """ACP-like RPC bridge service."""

    def __init__(self, protocol: ProtocolService):
        self.protocol = protocol

    def connect(self) -> dict:
        return self.protocol.call("health")

    def call(self, method: str, params_json: str) -> dict:
        params = json.loads(params_json) if params_json else {}
        if not isinstance(params, dict):
            raise ValueError("params must be JSON object")
        return self.protocol.call(method, params)


class DnsService:
    """DNS helpers."""

    def lookup(self, host: str) -> list[str]:
        infos = socket.getaddrinfo(host, None)
        return sorted({str(item[4][0]) for item in infos if item and item[4]})

    def setup(self, domain: str, apply: bool = False, dry_run: bool = False) -> dict[str, Any]:
        data_dir = get_data_dir()
        out = {
            "domain": domain.strip(),
            "apply": bool(apply),
            "dry_run": bool(dry_run),
            "zone_file": str(Path(data_dir) / "dns" / f"{domain.strip().replace('.', '_')}.zone"),
            "steps": [
                "ensure dns directory exists",
                "prepare zone file template",
                "print tailscale/coredns instructions",
            ],
        }
        if apply and not dry_run:
            (Path(data_dir) / "dns").mkdir(parents=True, exist_ok=True)
        return out

