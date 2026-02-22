"""Directory and workspace lookup services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from joyhousebot.config.loader import load_config
from joyhousebot.cli.services.state_service import StateService


class DirectoryService:
    """Directory browsing helpers."""

    def __init__(self, state: StateService):
        self.state = state

    def list(self, path: str) -> tuple[str, list[dict[str, str]]]:
        cfg = load_config()
        base = Path(path).expanduser() if path else cfg.workspace_path
        if not base.exists() or not base.is_dir():
            raise ValueError(f"Not a directory: {base}")
        rows: list[dict[str, str]] = []
        for p in sorted(base.iterdir(), key=lambda x: x.name.lower()):
            rows.append(
                {
                    "name": p.name,
                    "type": "dir" if p.is_dir() else "file",
                    "size": "-" if p.is_dir() else str(p.stat().st_size),
                }
            )
        return str(base), rows

    def agent_workspaces(self) -> list[dict[str, str]]:
        cfg = load_config()
        rows: list[dict[str, str]] = []
        for entry in cfg.agents.agent_list or []:
            ws = Path(entry.workspace).expanduser()
            rows.append({"agent": entry.id, "workspace": str(ws), "exists": "yes" if ws.exists() else "no"})
        return rows

    def _channel_store(self) -> dict[str, Any]:
        return self.state.read_json(
            "directory_channels",
            {
                "self": {"id": "me", "name": "Operator"},
                "peers": [],
                "groups": [],
                "members": {},
            },
        )

    def self_entry(self) -> dict[str, str]:
        payload = self._channel_store()
        me = payload.get("self") if isinstance(payload.get("self"), dict) else {}
        return {"id": str(me.get("id", "me")), "name": str(me.get("name", "Operator"))}

    def list_peers(self, query: str, limit: int) -> list[dict[str, str]]:
        payload = self._channel_store()
        rows = payload.get("peers") if isinstance(payload.get("peers"), list) else []
        q = query.strip().lower()
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id", ""))
            name = str(row.get("name", ""))
            if q and q not in rid.lower() and q not in name.lower():
                continue
            result.append({"id": rid, "name": name})
            if len(result) >= max(1, limit):
                break
        return result

    def list_groups(self, query: str, limit: int) -> list[dict[str, str]]:
        payload = self._channel_store()
        rows = payload.get("groups") if isinstance(payload.get("groups"), list) else []
        q = query.strip().lower()
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id", ""))
            name = str(row.get("name", ""))
            if q and q not in rid.lower() and q not in name.lower():
                continue
            result.append({"id": rid, "name": name})
            if len(result) >= max(1, limit):
                break
        return result

    def list_group_members(self, group_id: str, limit: int) -> list[dict[str, str]]:
        payload = self._channel_store()
        members = payload.get("members") if isinstance(payload.get("members"), dict) else {}
        rows = members.get(group_id) if isinstance(members.get(group_id), list) else []
        result = []
        for row in rows[: max(1, limit)]:
            if not isinstance(row, dict):
                continue
            result.append({"id": str(row.get("id", "")), "name": str(row.get("name", ""))})
        return result

