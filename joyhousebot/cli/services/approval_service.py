"""Execution approval workflow services."""

from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from joyhousebot.cli.services.protocol_service import ProtocolService
from joyhousebot.cli.services.state_service import StateService


class ApprovalService:
    def _parse_policy_input(self, file_json: str) -> dict[str, Any]:
        raw = file_json.strip()
        candidate = Path(raw).expanduser()
        if raw and candidate.exists() and candidate.is_file():
            raw = candidate.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("file must be a JSON object or a JSON file path")
        parsed.setdefault("version", 1)
        return parsed

    """Approval and policy operations."""

    def __init__(self, protocol: ProtocolService, state: StateService):
        self.protocol = protocol
        self.state = state

    def request(self, command: str, timeout_ms: int, request_id: str) -> dict:
        return self.protocol.call(
            "exec.approval.request",
            {"id": request_id or None, "command": command, "timeoutMs": max(1000, timeout_ms)},
        )

    def wait(self, request_id: str) -> dict:
        return self.protocol.call("exec.approval.waitDecision", {"id": request_id})

    def resolve(self, request_id: str, decision: str) -> dict:
        return self.protocol.call("exec.approval.resolve", {"requestId": request_id, "decision": decision})

    def _normalize_file(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        file_obj = payload.get("file") if isinstance(payload, dict) else None
        if isinstance(file_obj, dict):
            file_obj.setdefault("version", 1)
            return file_obj
        return {"version": 1, "agents": {}}

    def _snapshot_local(self) -> dict[str, Any]:
        file_obj = self.state.read_json("approvals_policy", {"version": 1, "agents": {}})
        path = str(self.state.path_of("approvals_policy"))
        return {
            "target": "local",
            "path": path,
            "exists": self.state.path_of("approvals_policy").exists(),
            "hash": self.state.json_hash(file_obj),
            "file": file_obj,
        }

    def _snapshot_remote(self, node_id: str) -> dict[str, Any]:
        if node_id:
            payload = self.protocol.call("exec.approvals.node.get", {"nodeId": node_id})
            target = "node"
        else:
            payload = self.protocol.call("exec.approvals.get")
            target = "gateway"
        file_obj = self._normalize_file(payload if isinstance(payload, dict) else {})
        res_hash = ""
        if isinstance(payload, dict):
            res_hash = str(payload.get("hash") or "")
        return {
            "target": target,
            "nodeId": node_id or None,
            "path": f"rpc://{target}",
            "exists": True,
            "hash": res_hash or self.state.json_hash(file_obj),
            "file": file_obj,
        }

    def policy_get(self, target: str = "local", node_id: str = "") -> dict:
        normalized = target.strip().lower()
        if normalized == "local":
            return self._snapshot_local()
        if normalized in {"gateway", "node"}:
            if normalized == "node" and not node_id.strip():
                raise ValueError("node target requires --node-id")
            return self._snapshot_remote(node_id.strip())
        raise ValueError("target must be local|gateway|node")

    def policy_set(
        self,
        file_json: str,
        target: str = "local",
        node_id: str = "",
        base_hash: str = "",
    ) -> dict:
        parsed = self._parse_policy_input(file_json)
        normalized = target.strip().lower()
        if normalized == "local":
            current = self._snapshot_local()
            if base_hash and base_hash != current["hash"]:
                raise RuntimeError("POLICY_CONFLICT: baseHash mismatch, reload policy and retry")
            self.state.write_json("approvals_policy", parsed)
            return self._snapshot_local()
        if normalized == "gateway":
            return self.protocol.call(
                "exec.approvals.set",
                {"file": parsed, "baseHash": base_hash or None},
            )
        if normalized == "node":
            if not node_id.strip():
                raise ValueError("node target requires --node-id")
            return self.protocol.call(
                "exec.approvals.node.set",
                {"nodeId": node_id.strip(), "file": parsed, "baseHash": base_hash or None},
            )
        raise ValueError("target must be local|gateway|node")

    def _load_mutable_policy(self, target: str, node_id: str) -> tuple[dict[str, Any], str]:
        snap = self.policy_get(target=target, node_id=node_id)
        return deepcopy(self._normalize_file(snap)), str(snap.get("hash") or "")

    def allowlist_list(self, target: str = "local", node_id: str = "", agent: str = "*") -> dict:
        file_obj, base_hash = self._load_mutable_policy(target, node_id)
        agents = file_obj.get("agents") if isinstance(file_obj.get("agents"), dict) else {}
        selected = agents.get(agent, {}) if isinstance(agents, dict) else {}
        allowlist = selected.get("allowlist") if isinstance(selected, dict) else []
        entries = allowlist if isinstance(allowlist, list) else []
        return {
            "target": target,
            "nodeId": node_id or None,
            "agent": agent,
            "baseHash": base_hash,
            "entries": entries,
        }

    def allowlist_add(
        self,
        pattern: str,
        target: str = "local",
        node_id: str = "",
        agent: str = "*",
        base_hash: str = "",
    ) -> dict:
        p = pattern.strip()
        if not p:
            raise ValueError("pattern cannot be empty")
        file_obj, current_hash = self._load_mutable_policy(target, node_id)
        write_hash = base_hash or current_hash
        agents = file_obj.setdefault("agents", {})
        if not isinstance(agents, dict):
            agents = {}
            file_obj["agents"] = agents
        agent_obj = agents.setdefault(agent, {})
        if not isinstance(agent_obj, dict):
            agent_obj = {}
            agents[agent] = agent_obj
        allowlist = agent_obj.setdefault("allowlist", [])
        if not isinstance(allowlist, list):
            allowlist = []
            agent_obj["allowlist"] = allowlist
        if not any(str(x.get("pattern", "")).strip() == p for x in allowlist if isinstance(x, dict)):
            allowlist.append({"pattern": p, "lastUsedAt": int(time.time() * 1000)})
        return self.policy_set(
            json.dumps(file_obj, ensure_ascii=False),
            target=target,
            node_id=node_id,
            base_hash=write_hash,
        )

    def allowlist_remove(
        self,
        pattern: str,
        target: str = "local",
        node_id: str = "",
        agent: str = "*",
        base_hash: str = "",
    ) -> dict:
        p = pattern.strip()
        if not p:
            raise ValueError("pattern cannot be empty")
        file_obj, current_hash = self._load_mutable_policy(target, node_id)
        write_hash = base_hash or current_hash
        agents = file_obj.get("agents") if isinstance(file_obj.get("agents"), dict) else {}
        agent_obj = agents.get(agent, {}) if isinstance(agents, dict) else {}
        allowlist = agent_obj.get("allowlist") if isinstance(agent_obj, dict) else []
        entries = allowlist if isinstance(allowlist, list) else []
        agent_obj["allowlist"] = [
            x for x in entries if not (isinstance(x, dict) and str(x.get("pattern", "")).strip() == p)
        ]
        if isinstance(agents, dict):
            agents[agent] = agent_obj
            file_obj["agents"] = agents
        return self.policy_set(
            json.dumps(file_obj, ensure_ascii=False),
            target=target,
            node_id=node_id,
            base_hash=write_hash,
        )

