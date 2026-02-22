"""Browser protocol service."""

from __future__ import annotations

from joyhousebot.cli.services.protocol_service import ProtocolService


class BrowserService:
    """Browser requests via gateway protocol."""

    def __init__(self, protocol: ProtocolService):
        self.protocol = protocol

    def request(self, method: str, path: str, node_id: str, timeout_ms: int) -> dict:
        return self.protocol.call(
            "browser.request",
            {
                "method": method.upper(),
                "path": path,
                "nodeId": node_id or None,
                "timeoutMs": max(100, timeout_ms),
            },
        )

    def status(self, node_id: str, timeout_ms: int) -> dict:
        return self.request("GET", "/status", node_id, timeout_ms)

    def inspect(self, path: str, node_id: str, timeout_ms: int) -> dict:
        target = path if path.startswith("/") else f"/inspect/{path}"
        return self.request("GET", target, node_id, timeout_ms)

    def action(self, action: str, payload_json: str, node_id: str, timeout_ms: int) -> dict:
        import json

        payload = json.loads(payload_json) if payload_json else {}
        if not isinstance(payload, dict):
            raise ValueError("payload must be JSON object")
        return self.protocol.call(
            "browser.request",
            {
                "method": "POST",
                "path": f"/action/{action.strip()}",
                "nodeId": node_id or None,
                "timeoutMs": max(100, timeout_ms),
                "payload": payload,
            },
        )

    def debug(self, enabled: bool, node_id: str, timeout_ms: int) -> dict:
        return self.protocol.call(
            "browser.request",
            {
                "method": "POST",
                "path": "/debug",
                "nodeId": node_id or None,
                "timeoutMs": max(100, timeout_ms),
                "payload": {"enabled": bool(enabled)},
            },
        )

    def state(self, key: str, value_json: str, node_id: str, timeout_ms: int) -> dict:
        import json

        payload = {"key": key.strip()}
        if value_json:
            payload["value"] = json.loads(value_json)
        return self.protocol.call(
            "browser.request",
            {
                "method": "POST",
                "path": "/state",
                "nodeId": node_id or None,
                "timeoutMs": max(100, timeout_ms),
                "payload": payload,
            },
        )

