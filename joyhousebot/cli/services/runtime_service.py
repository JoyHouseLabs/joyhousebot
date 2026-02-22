"""Runtime/system/docs/update/uninstall and pass-through helpers."""

from __future__ import annotations

import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
import time

from joyhousebot import __version__
from joyhousebot.cli.shared.http_utils import get_gateway_base_url, http_json
from joyhousebot.cli.services.protocol_service import ProtocolService
from joyhousebot.config.loader import get_data_dir, load_config, save_config


class RuntimeService:
    """Runtime operations compatible with protocol semantics."""

    def __init__(self, protocol: ProtocolService):
        self.protocol = protocol

    def system_status_data(self) -> dict:
        data: dict = {}
        base = get_gateway_base_url()
        try:
            data["health"] = http_json("GET", f"{base}/health", timeout=2.0)
            data["gateway_ok"] = True
        except Exception as exc:
            data["gateway_ok"] = False
            data["gateway_error"] = str(exc)
        try:
            presence = self.protocol.call("system-presence")
            data["presence_entries"] = len(presence if isinstance(presence, list) else [])
        except Exception:
            data["presence_entries"] = None
        try:
            heartbeat = self.protocol.call("last-heartbeat")
            data["last_heartbeat"] = heartbeat.get("ts")
        except Exception:
            data["last_heartbeat"] = None
        return data

    def system_presence(self) -> dict:
        return self.protocol.call("system-presence")

    def system_logs(self, limit: int) -> dict:
        return self.protocol.call("logs.tail", {"limit": max(1, limit)})

    def docs_target(self, topic: str) -> tuple[bool, str]:
        if topic.strip():
            url = f"https://github.com/JoyHouseLabs/joyhousebot/search?q={topic.strip()}"
        else:
            url = "https://github.com/JoyHouseLabs/joyhousebot"
        opened = webbrowser.open(url)
        if opened:
            return True, url
        return False, str(Path(__file__).resolve().parents[3] / "docs")

    def update_info(
        self,
        run: bool,
        status: bool = False,
        channel: str = "",
        tag: str = "",
        wizard: bool = False,
    ) -> dict:
        payload = {
            "installed_version": __version__,
            "recommended_command": "pip install -U joyhousebot-ai",
        }
        if status:
            try:
                meta = self.protocol.call("update.status")
            except Exception as exc:
                meta = {"supported": False, "error": str(exc)}
            payload["status"] = meta
        if channel.strip():
            cfg = load_config()
            if hasattr(cfg, "runtime") and hasattr(cfg.runtime, "update_channel"):
                cfg.runtime.update_channel = channel.strip()
                save_config(cfg)
            payload["channel"] = channel.strip()
        if tag.strip():
            payload["tag"] = tag.strip()
        if wizard:
            payload["wizard"] = {
                "steps": ["check version", "choose channel/tag", "trigger update.run"],
                "ts": int(time.time() * 1000),
            }
        if run:
            payload["update_run"] = self.protocol.call("update.run")
        return payload

    def uninstall_targets(self, keep_config: bool, scope: str = "all") -> list[str]:
        normalized = scope.strip().lower() or "all"
        targets: list[Path] = []
        if normalized in {"all", "state"}:
            targets.extend([get_data_dir(), Path.home() / ".joyhousebot" / "bridge"])
        if normalized in {"all", "config"} and not keep_config:
            targets.append(Path.home() / ".joyhousebot" / "config.json")
        if normalized in {"all", "workspace"}:
            workspace = self._workspace_from_protocol() or str(load_config().agents.defaults.workspace)
            targets.append(Path(workspace).expanduser())
        return [str(x) for x in targets]

    def perform_uninstall(self, targets: list[str], keep_config: bool, dry_run: bool = False) -> dict:
        cfg = load_config()
        removed: list[str] = []
        for raw in targets:
            target = Path(raw)
            if not target.exists():
                continue
            if dry_run:
                removed.append(f"would_remove:{target}")
                continue
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed.append(str(target))
        if keep_config:
            save_config(cfg)
        return {"ok": True, "dry_run": dry_run, "removed": removed}

    def run_agent(self, *args: str) -> int:
        return subprocess.call([sys.executable, "-m", "joyhousebot.cli.commands", "agent", *args])

    def run_channels(self, *args: str) -> int:
        return subprocess.call([sys.executable, "-m", "joyhousebot.cli.commands", "channels", *args])

    def run_skills(self, *args: str) -> int:
        return subprocess.call([sys.executable, "-m", "joyhousebot.cli.commands", "skills", *args])

    def run_plugins(self, *args: str) -> int:
        return subprocess.call([sys.executable, "-m", "joyhousebot.cli.commands", "plugins", *args])

    def _workspace_from_protocol(self) -> str | None:
        try:
            snapshot = self.protocol.call("config.get", {})
        except Exception:
            return None
        parsed = snapshot.get("parsed") if isinstance(snapshot, dict) else {}
        if not isinstance(parsed, dict):
            return None
        agents = parsed.get("agents")
        if not isinstance(agents, dict):
            return None
        defaults = agents.get("defaults")
        if not isinstance(defaults, dict):
            return None
        workspace = defaults.get("workspace")
        return str(workspace) if workspace else None

