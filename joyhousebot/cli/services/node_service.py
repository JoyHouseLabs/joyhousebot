"""Node control services."""

from __future__ import annotations

import json
import time
from typing import Any

from joyhousebot.cli.services.protocol_service import ProtocolService
from joyhousebot.cli.services.state_service import StateService


class NodeService:
    """Node methods mapped to protocol calls."""

    def __init__(self, protocol: ProtocolService, state: StateService):
        self.protocol = protocol
        self.state = state

    def list(self) -> dict:
        return self.protocol.call("node.list")

    def resolve_node_id(self, query: str) -> str:
        raw = query.strip()
        if not raw:
            raise ValueError("node reference cannot be empty")
        payload = self.list()
        rows = payload.get("nodes") if isinstance(payload, dict) else []
        nodes = [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []
        if not nodes:
            raise ValueError("no known nodes from gateway")

        def node_id(item: dict[str, Any]) -> str:
            return str(item.get("nodeId", "")).strip()

        for item in nodes:
            if node_id(item) == raw:
                return raw

        raw_lower = raw.lower()
        matches: list[dict[str, Any]] = []
        for item in nodes:
            display_name = str(item.get("displayName", "")).strip().lower()
            remote_ip = str(item.get("remoteIp", "")).strip()
            if display_name == raw_lower or remote_ip == raw:
                matches.append(item)
        if not matches:
            raise ValueError(f"node not found: {raw}")
        if len(matches) > 1:
            candidates = ", ".join(node_id(x) for x in matches if node_id(x))
            raise ValueError(f"ambiguous node reference '{raw}', matches: {candidates}")
        resolved = node_id(matches[0])
        if not resolved:
            raise ValueError(f"invalid node data for reference: {raw}")
        return resolved

    def describe(self, node_id: str) -> dict:
        return self.protocol.call("node.describe", {"nodeId": node_id})

    def rename(self, node_id: str, name: str) -> dict:
        return self.protocol.call("node.rename", {"nodeId": node_id, "displayName": name})

    def invoke(self, node_id: str, command: str, params_json: str, timeout_ms: int) -> dict:
        params = json.loads(params_json) if params_json else {}
        if not isinstance(params, dict):
            raise ValueError("params must be a JSON object")
        return self.protocol.call(
            "node.invoke",
            {
                "nodeId": node_id,
                "command": command,
                "params": params,
                "timeoutMs": max(100, timeout_ms),
            },
        )

    def pair_list(self) -> dict:
        return self.protocol.call("node.pair.list")

    def pair_approve(self, request_id: str) -> dict:
        return self.protocol.call("node.pair.approve", {"requestId": request_id})

    def pair_reject(self, request_id: str) -> dict:
        return self.protocol.call("node.pair.reject", {"requestId": request_id})

    def pair_verify(self, node_id: str, token: str) -> dict:
        return self.protocol.call("node.pair.verify", {"nodeId": node_id, "token": token})

    def _host_state(self) -> dict:
        return self.state.read_json(
            "node_host",
            {"installed": False, "running": False, "runtime": "python", "updatedAtMs": 0},
        )

    def _try_rpc(self, method: str, params: dict | None = None) -> dict | None:
        try:
            return self.protocol.call(method, params or {})
        except Exception:
            return None

    def run_host(self, host: str, port: int, tls: bool, node_id: str, display_name: str) -> dict:
        remote = self._try_rpc(
            "node.host.run",
            {
                "host": host,
                "port": int(port),
                "tls": bool(tls),
                "nodeId": node_id or None,
                "displayName": display_name or None,
            },
        )
        if remote is not None:
            return remote
        state = self._host_state()
        state.update(
            {
                "running": True,
                "host": host,
                "port": int(port),
                "tls": bool(tls),
                "nodeId": node_id or None,
                "displayName": display_name or None,
                "updatedAtMs": int(time.time() * 1000),
            }
        )
        self.state.write_json("node_host", state)
        return state

    def install_host(self, runtime: str, force: bool) -> dict:
        remote = self._try_rpc("node.host.install", {"runtime": runtime or "python", "force": bool(force)})
        if remote is not None:
            return remote
        state = self._host_state()
        state["installed"] = True
        state["runtime"] = runtime or "python"
        state["force"] = bool(force)
        state["updatedAtMs"] = int(time.time() * 1000)
        self.state.write_json("node_host", state)
        return state

    def host_status(self) -> dict:
        remote = self._try_rpc("node.host.status")
        if remote is not None:
            return remote
        return self._host_state()

    def stop_host(self) -> dict:
        remote = self._try_rpc("node.host.stop")
        if remote is not None:
            return remote
        state = self._host_state()
        state["running"] = False
        state["updatedAtMs"] = int(time.time() * 1000)
        self.state.write_json("node_host", state)
        return state

    def restart_host(self) -> dict:
        remote = self._try_rpc("node.host.restart")
        if remote is not None:
            return remote
        state = self._host_state()
        state["running"] = True
        state["updatedAtMs"] = int(time.time() * 1000)
        self.state.write_json("node_host", state)
        return state

    def uninstall_host(self) -> dict:
        remote = self._try_rpc("node.host.uninstall")
        if remote is not None:
            return remote
        state = self._host_state()
        state["installed"] = False
        state["running"] = False
        state["updatedAtMs"] = int(time.time() * 1000)
        self.state.write_json("node_host", state)
        return state

