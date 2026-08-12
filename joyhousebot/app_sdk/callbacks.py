"""Verification of signed terminal Run callbacks delivered to an App."""

from __future__ import annotations

import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

from joyhousebot.domain.app_callbacks import callback_body, callback_signature


@dataclass(frozen=True, slots=True)
class VerifiedAppCallback:
    event_id: str
    event_type: str
    payload: dict[str, Any]
    replay_of_event_id: str | None
    replay_sequence: int


def verify_app_callback(
    headers: Mapping[str, str],
    body: bytes,
    *,
    secret: bytes | str,
    now: int | None = None,
    tolerance_seconds: int = 300,
) -> VerifiedAppCallback:
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    try:
        timestamp = int(normalized["x-joyhouse-timestamp"])
        signature = normalized["x-joyhouse-signature"]
        event_id = normalized["x-joyhouse-event-id"]
        event_type = normalized["x-joyhouse-event-type"]
    except (KeyError, ValueError) as exc:
        raise ValueError("JoyhouseBot callback signature headers are invalid") from exc
    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > max(1, int(tolerance_seconds)):
        raise ValueError("JoyhouseBot callback timestamp is outside the allowed window")
    secret_bytes = secret.encode() if isinstance(secret, str) else secret
    expected = callback_signature(secret_bytes, timestamp=str(timestamp), body=body)
    if not hmac.compare_digest(signature, expected):
        raise ValueError("JoyhouseBot callback signature does not match")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("JoyhouseBot callback body is not JSON") from exc
    if not isinstance(payload, dict) or callback_body(payload) != body:
        raise ValueError("JoyhouseBot callback body is not canonical JSON")
    if str(payload.get("event_id") or "") != event_id:
        raise ValueError("JoyhouseBot callback event identity does not match")
    if str(payload.get("event_type") or "") != event_type:
        raise ValueError("JoyhouseBot callback event type does not match")
    delivery = dict(payload.get("delivery") or {})
    return VerifiedAppCallback(
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        replay_of_event_id=(
            str(delivery["replay_of_event_id"])
            if delivery.get("replay_of_event_id")
            else None
        ),
        replay_sequence=int(delivery.get("replay_sequence") or 0),
    )


__all__ = ["VerifiedAppCallback", "verify_app_callback"]
