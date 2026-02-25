#!/usr/bin/env python3
"""OpenClaw RPC compatibility smoke checks.

Usage:
  python scripts/rpc_compat_smoke.py
  python scripts/rpc_compat_smoke.py --openclaw-root /abs/path/to/openclaw
"""

from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path

from joyhousebot.api.server import GATEWAY_METHODS, RpcClientState, _handle_rpc_request, app_state
from joyhousebot.config.loader import load_config
from joyhousebot.node import NodeRegistry, NodeSession


METHOD_RE = re.compile(r'"([a-z]+(?:\.[a-z]+)+)"\s*:\s*async')


def collect_openclaw_methods(openclaw_root: Path) -> set[str]:
    methods: set[str] = set()
    methods_dir = openclaw_root / "src" / "gateway" / "server-methods"
    for ts_file in methods_dir.glob("*.ts"):
        text = ts_file.read_text(encoding="utf-8")
        for m in METHOD_RE.finditer(text):
            methods.add(m.group(1))
    return methods


class _FakeNodeSocket:
    def __init__(self, registry: NodeRegistry):
        self.registry = registry

    async def send_json(self, data):
        payload = data.get("payload") or {}
        invoke_id = payload.get("id")
        node_id = payload.get("nodeId")
        command = payload.get("command")

        async def done():
            await asyncio.sleep(0.01)
            if command == "browser.proxy":
                self.registry.handle_invoke_result(
                    invoke_id=invoke_id,
                    node_id=node_id,
                    ok=True,
                    payload={"result": {"ok": True, "path": "/tmp/browser-result.json"}},
                )
            else:
                self.registry.handle_invoke_result(
                    invoke_id=invoke_id,
                    node_id=node_id,
                    ok=True,
                    payload={"ok": True, "echo": command},
                )

        asyncio.create_task(done())


async def _rpc_call(client: RpcClientState, method: str, params: dict | None = None):
    return await _handle_rpc_request(
        {"type": "req", "id": "smoke", "method": method, "params": params or {}},
        client,
        f"smoke-{client.client_id or client.role}",
    )


def _is_not_paired_error(err) -> bool:
    """True if error indicates device is not paired (e.g. in CI)."""
    if isinstance(err, dict):
        if err.get("code") == "NOT_PAIRED":
            return True
        msg = str(err.get("message", "")).lower()
        if "device identity" in msg or "not paired" in msg:
            return True
    return False


async def run_runtime_smoke() -> tuple[list[str], bool]:
    errors: list[str] = []
    cfg = load_config()
    app_state["config"] = cfg
    registry = NodeRegistry()
    app_state["node_registry"] = registry
    await registry.register(
        node=NodeSession(
            node_id="smoke-node",
            conn_id="smoke-conn",
            platform="ios",
            device_family="iphone",
            caps=["browser", "canvas"],
            commands=["browser.proxy", "canvas.snapshot"],
            connected_at_ms=1,
        ),
        socket=_FakeNodeSocket(registry),
    )
    op = RpcClientState(connected=True, role="operator", scopes={"operator.admin"}, client_id="smoke-op")
    node = RpcClientState(connected=True, role="node", scopes=set(), client_id="smoke-node")

    checks = [
        ("status", {}),
        ("actions.catalog", {}),
        ("actions.validate", {"code": "AUTH_PROVIDER_DOWN", "action": {"type": "navigate", "target": "settings.auth.provider"}}),
        (
            "actions.validate.batch.lifecycle",
            {"items": [{"code": "AUTH_PROVIDER_DOWN", "action": {"type": "navigate", "target": "settings.auth.provider"}}]},
        ),
        ("alerts.lifecycle", {}),
        ("node.list", {}),
        ("node.describe", {"nodeId": "smoke-node"}),
        ("node.invoke", {"nodeId": "smoke-node", "command": "canvas.snapshot"}),
        ("browser.request", {"method": "GET", "path": "/status"}),
        ("exec.approval.request", {"id": "smoke-approval", "command": "ls", "twoPhase": True}),
        ("exec.approval.resolve", {"id": "smoke-approval", "decision": "allow-once"}),
        ("exec.approval.waitDecision", {"id": "smoke-approval"}),
    ]
    for method, params in checks:
        ok, payload, err = await _rpc_call(op, method, params)
        if not ok:
            errors.append(f"{method} failed: {err}")
        elif payload is None:
            errors.append(f"{method} returned empty payload")

    # End-to-end connect snapshot smoke (fresh client must call connect first).
    fresh = RpcClientState()
    ok, payload, err = await _rpc_call(fresh, "connect", {"role": "operator", "scopes": ["operator.read"], "clientId": "smoke-connect"})
    if not ok:
        if _is_not_paired_error(err):
            # CI / unpaired environment: skip connect and downstream checks that require device identity
            return errors, True
        errors.append(f"connect failed: {err}")
    else:
        snap = payload.get("snapshot") if isinstance(payload, dict) else {}
        if not isinstance(snap, dict) or not isinstance(snap.get("alertsSummary"), dict):
            errors.append("connect snapshot missing alertsSummary")
        if not isinstance(snap.get("actionsCatalog"), dict):
            errors.append("connect snapshot missing actionsCatalog")
        if not isinstance(snap.get("alertsLifecycle"), dict):
            errors.append("connect snapshot missing alertsLifecycle")

        ok, _, err = await _rpc_call(node, "node.event", {"event": "chat.subscribe", "payload": {"sessionKey": "main"}})
        if not ok:
            errors.append(f"node.event(chat.subscribe) failed: {err}")

    return errors, False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--openclaw-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "openclaw",
        help="Path to openclaw repository root",
    )
    args = parser.parse_args()

    openclaw_methods = collect_openclaw_methods(args.openclaw_root)
    ours = set(GATEWAY_METHODS)
    missing = sorted(openclaw_methods - ours)
    extra = sorted(ours - openclaw_methods)

    print(f"openclaw methods: {len(openclaw_methods)}")
    print(f"joyhousebot methods: {len(ours)}")
    print(f"missing methods: {len(missing)}")
    for item in missing:
        print(f"  MISSING: {item}")
    print(f"extra methods: {len(extra)}")

    runtime_errors, skipped = asyncio.run(run_runtime_smoke())
    if skipped:
        print("runtime smoke skipped (no device paired)")
    if runtime_errors:
        print("runtime smoke errors:")
        for err in runtime_errors:
            print(f"  ERROR: {err}")
        return 1

    if missing:
        return 2
    print("rpc_compat_smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
