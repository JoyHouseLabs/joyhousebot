"""Dependency-free verification for canonical joyhousebot callbacks."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class VerifiedCallback:
    event_id: str
    event_type: str
    payload: dict[str, Any]

    @property
    def run_id(self) -> str:
        run = self.payload.get("run")
        return str(run.get("run_id") or run.get("id") or "") if isinstance(run, dict) else ""

    @property
    def replay_of_event_id(self) -> str:
        delivery = self.payload.get("delivery")
        return str(delivery.get("replay_of_event_id") or "") if isinstance(delivery, dict) else ""

    @property
    def replay_sequence(self) -> int:
        delivery = self.payload.get("delivery")
        return int(delivery.get("replay_sequence") or 0) if isinstance(delivery, dict) else 0


def verify_callback(
    headers: Mapping[str, str],
    body: bytes,
    *,
    secret: str | bytes,
    now: int | None = None,
    tolerance_seconds: int = 300,
) -> VerifiedCallback:
    if len(secret.encode() if isinstance(secret, str) else secret) < 32:
        raise ValueError("joyhousebot callback secret must contain at least 32 bytes")
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    try:
        timestamp = int(normalized["x-joyhousebot-timestamp"])
        signature = normalized["x-joyhousebot-signature"]
        event_id = normalized["x-joyhousebot-event-id"]
        event_type = normalized["x-joyhousebot-event-type"]
    except (KeyError, ValueError) as exc:
        raise ValueError("joyhousebot callback signature headers are invalid") from exc
    if abs((int(time.time()) if now is None else int(now)) - timestamp) > tolerance_seconds:
        raise ValueError("joyhousebot callback timestamp is outside the allowed window")
    secret_bytes = secret.encode() if isinstance(secret, str) else secret
    digest = hmac.new(secret_bytes, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, f"v1={digest}"):
        raise ValueError("joyhousebot callback signature does not match")
    value = json.loads(body)
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    if not isinstance(value, dict) or canonical != body:
        raise ValueError("joyhousebot callback body is not canonical JSON")
    if value.get("event_id") != event_id or value.get("event_type") != event_type:
        raise ValueError("joyhousebot callback event identity does not match")
    return VerifiedCallback(event_id=event_id, event_type=event_type, payload=value)


__all__ = ["VerifiedCallback", "verify_callback"]
