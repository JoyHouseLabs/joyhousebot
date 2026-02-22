"""Device auth payload builder for connect handshake (OpenClaw-aligned)."""

from __future__ import annotations


def build_device_auth_payload(
    *,
    device_id: str,
    client_id: str,
    client_mode: str,
    role: str,
    scopes: list[str],
    signed_at_ms: int,
    token: str | None = None,
    nonce: str | None = None,
    version: str = "v2",
) -> str:
    """Build canonical payload string for device signature (v1 or v2 with nonce)."""
    scope_str = ",".join(scopes)
    token_val = token or ""
    if version == "v2":
        parts = [
            "v2",
            device_id,
            client_id,
            client_mode,
            role,
            scope_str,
            str(signed_at_ms),
            token_val,
            nonce or "",
        ]
    else:
        parts = [
            "v1",
            device_id,
            client_id,
            client_mode,
            role,
            scope_str,
            str(signed_at_ms),
            token_val,
        ]
    return "|".join(parts)
