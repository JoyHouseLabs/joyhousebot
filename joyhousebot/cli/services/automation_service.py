"""Hooks and webhooks services."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from joyhousebot.cli.services.state_service import StateService
from joyhousebot.cli.shared.http_utils import http_json
from joyhousebot.config.loader import get_data_dir


class HooksService:
    """Manage local CLI hooks."""

    def __init__(self, state: StateService):
        self.state = state

    def list(self) -> dict:
        return self.state.read_json("hooks", {"before": [], "after": []})

    def add(self, stage: str, command: str) -> dict:
        payload = self.list()
        payload[stage].append(command)
        self.state.write_json("hooks", payload)
        return payload

    def remove(self, stage: str, index: int) -> str:
        payload = self.list()
        removed = payload[stage].pop(index)
        self.state.write_json("hooks", payload)
        return removed

    def run(self, stage: str) -> list[str]:
        payload = self.list()
        cmds = payload.get(stage) if isinstance(payload.get(stage), list) else []
        for cmd in cmds:
            subprocess.run(cmd, shell=True, check=False)
        return [str(c) for c in cmds]

    def check(self) -> dict:
        payload = self.list()
        before = payload.get("before") if isinstance(payload.get("before"), list) else []
        after = payload.get("after") if isinstance(payload.get("after"), list) else []
        return {
            "total": len(before) + len(after),
            "stages": {
                "before": {"count": len(before), "ready": all(bool(str(x).strip()) for x in before)},
                "after": {"count": len(after), "ready": all(bool(str(x).strip()) for x in after)},
            },
        }

    def install(self, source: str, link: bool = False) -> dict:
        installs = self.state.read_json("hooks_installs", {"entries": []})
        entries = installs.get("entries") if isinstance(installs.get("entries"), list) else []
        src = Path(source).expanduser()
        pack_root = get_data_dir() / "hooks" / "packs"
        pack_root.mkdir(parents=True, exist_ok=True)
        install_id = f"hookpack-{int(time.time())}"
        install_path = ""
        imported = {"before": 0, "after": 0}

        if src.exists():
            if link:
                install_path = str(src)
            else:
                target = pack_root / install_id
                if src.is_dir():
                    shutil.copytree(src, target, dirs_exist_ok=True)
                else:
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, target / src.name)
                install_path = str(target)
            imported = self._import_manifest(Path(install_path))

        item = {
            "id": install_id,
            "source": source.strip(),
            "link": bool(link),
            "installPath": install_path or None,
            "installedAtMs": int(time.time() * 1000),
            "imported": imported,
        }
        entries.append(item)
        installs["entries"] = entries
        self.state.write_json("hooks_installs", installs)
        return item

    def update(self, all_items: bool = False, target_id: str = "", dry_run: bool = False) -> dict:
        installs = self.state.read_json("hooks_installs", {"entries": []})
        entries = installs.get("entries") if isinstance(installs.get("entries"), list) else []
        targets = entries if all_items else [x for x in entries if str(x.get("id")) == target_id]
        outcomes = []
        for item in targets:
            imported = {"before": 0, "after": 0}
            install_path = Path(str(item.get("installPath") or "")).expanduser() if item.get("installPath") else None
            if install_path and install_path.exists() and not dry_run:
                imported = self._import_manifest(install_path)
            outcomes.append(
                {
                    "id": item.get("id"),
                    "status": "dry-run" if dry_run else "updated",
                    "imported": imported,
                }
            )
            if not dry_run:
                item["updatedAtMs"] = int(time.time() * 1000)
        self.state.write_json("hooks_installs", {"entries": entries})
        return {"outcomes": outcomes}

    def _import_manifest(self, root: Path) -> dict[str, int]:
        candidates = [root / "hooks.json", root / "manifest.json"]
        manifest = None
        for c in candidates:
            if c.exists() and c.is_file():
                try:
                    manifest = json.loads(c.read_text(encoding="utf-8"))
                except Exception:
                    manifest = None
                break
        if not isinstance(manifest, dict):
            return {"before": 0, "after": 0}
        before = manifest.get("before") if isinstance(manifest.get("before"), list) else []
        after = manifest.get("after") if isinstance(manifest.get("after"), list) else []
        hooks = self.list()
        hooks_before = hooks.get("before") if isinstance(hooks.get("before"), list) else []
        hooks_after = hooks.get("after") if isinstance(hooks.get("after"), list) else []
        add_before = [str(x).strip() for x in before if str(x).strip()]
        add_after = [str(x).strip() for x in after if str(x).strip()]
        hooks["before"] = list(dict.fromkeys(hooks_before + add_before))
        hooks["after"] = list(dict.fromkeys(hooks_after + add_after))
        self.state.write_json("hooks", hooks)
        return {"before": len(add_before), "after": len(add_after)}


class WebhooksService:
    """Manage simple outbound webhooks."""

    def __init__(self, state: StateService):
        self.state = state

    def list(self) -> dict:
        return self.state.read_json("webhooks", {"entries": []})

    def add(self, name: str, url: str, event: str) -> None:
        payload = self.list()
        entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
        entries = [e for e in entries if str(e.get("name")) != name]
        entries.append({"name": name, "url": url, "event": event, "enabled": True})
        payload["entries"] = entries
        self.state.write_json("webhooks", payload)

    def remove(self, name: str) -> None:
        payload = self.list()
        entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
        payload["entries"] = [e for e in entries if str(e.get("name")) != name]
        self.state.write_json("webhooks", payload)

    def test(self, name: str, payload_json: str) -> dict:
        payload = self.list()
        entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
        target = next((e for e in entries if str(e.get("name")) == name), None)
        if not target:
            raise ValueError(f"Webhook not found: {name}")
        body = json.loads(payload_json) if payload_json else {}
        if not isinstance(body, dict):
            raise ValueError("payload must be JSON object")
        return http_json("POST", str(target.get("url")), payload=body, timeout=8.0)

    def gmail_setup(self, account: str, hook_url: str, push_token: str, json_out: bool = False) -> dict:
        cfg = self.state.read_json("webhooks_gmail", {"accounts": []})
        accounts = cfg.get("accounts") if isinstance(cfg.get("accounts"), list) else []
        accounts = [x for x in accounts if str(x.get("account")) != account]
        accounts.append(
            {
                "account": account,
                "hookUrl": hook_url,
                "pushToken": push_token,
                "updatedAtMs": int(time.time() * 1000),
            }
        )
        cfg["accounts"] = accounts
        self.state.write_json("webhooks_gmail", cfg)
        return {"ok": True, "account": account, "json": bool(json_out)}

    def gmail_run(self, account: str, payload_json: str = "") -> dict:
        cfg = self.state.read_json("webhooks_gmail", {"accounts": []})
        accounts = cfg.get("accounts") if isinstance(cfg.get("accounts"), list) else []
        target = next((x for x in accounts if str(x.get("account")) == account), None)
        if not target:
            raise ValueError(f"gmail account not configured: {account}")
        body: dict[str, Any] = {
            "source": "gmail",
            "account": account,
            "type": "run",
            "ts": int(time.time() * 1000),
        }
        if payload_json.strip():
            extra = json.loads(payload_json)
            if not isinstance(extra, dict):
                raise ValueError("payload must be JSON object")
            body["payload"] = extra
        response = http_json("POST", str(target.get("hookUrl")), payload=body, timeout=8.0)
        return {"ok": True, "mode": "service", "account": account, "hookUrl": target.get("hookUrl"), "response": response}

