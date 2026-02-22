"""Device and pairing services."""

from __future__ import annotations

from joyhousebot.cli.services.protocol_service import ProtocolService


class DeviceService:
    """Device/pairing/token operations via protocol methods."""

    def __init__(self, protocol: ProtocolService):
        self.protocol = protocol

    def list_pairs(self) -> dict:
        return self.protocol.call("device.pair.list")

    def approve(self, request_id: str) -> dict:
        return self.protocol.call("device.pair.approve", {"requestId": request_id})

    def reject(self, request_id: str) -> dict:
        return self.protocol.call("device.pair.reject", {"requestId": request_id})

    def rotate_token(self, device_id: str, role: str, scopes_csv: str) -> dict:
        scopes = [x.strip() for x in scopes_csv.split(",") if x.strip()]
        return self.protocol.call(
            "device.token.rotate",
            {"deviceId": device_id or None, "role": role, "scopes": scopes},
        )

    def revoke_token(self, device_id: str) -> dict:
        return self.protocol.call("device.token.revoke", {"deviceId": device_id or None})

