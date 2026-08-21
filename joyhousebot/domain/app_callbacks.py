"""Validation and signing helpers for App completion callbacks."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

from joyhousebot.utils.ssrf import validate_url

APP_CALLBACK_EVENTS = frozenset(
    {"run.completed", "run.failed", "run.cancelled", "run.timed_out"}
)
_ENV_REFERENCE = re.compile(r"^env://([A-Za-z_][A-Za-z0-9_]*)$")


def normalize_app_callback(value: dict[str, Any]) -> dict[str, Any]:
    endpoint = str(value.get("endpoint") or "").strip()
    valid, reason = validate_url(endpoint)
    if not valid:
        raise ValueError(f"App callback endpoint is not allowed: {reason}")
    parsed = urlparse(endpoint)
    if parsed.scheme != "https":
        raise ValueError("App callback endpoint requires HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("App callback endpoint must not contain embedded credentials")
    secret_ref = str(value.get("secret_ref") or "").strip()
    if _ENV_REFERENCE.fullmatch(secret_ref) is None:
        raise ValueError("App callback secret_ref must use env://VARIABLE")
    events = sorted({str(item).strip() for item in value.get("events") or APP_CALLBACK_EVENTS})
    if not events or not set(events) <= APP_CALLBACK_EVENTS:
        raise ValueError("App callback events contain an unsupported terminal Run event")
    max_attempts = int(value.get("max_attempts") or 8)
    if not 1 <= max_attempts <= 20:
        raise ValueError("App callback max_attempts must be between 1 and 20")
    return {
        "endpoint": endpoint,
        "secret_ref": secret_ref,
        "events": events,
        "max_attempts": max_attempts,
    }


def resolve_callback_secret(reference: str) -> bytes:
    matched = _ENV_REFERENCE.fullmatch(str(reference))
    if matched is None:
        raise ValueError("App callback secret reference is invalid")
    value = os.environ.get(matched.group(1))
    if value is None or len(value.encode()) < 32:
        raise ValueError("App callback secret environment value must contain at least 32 bytes")
    return value.encode()


def callback_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def callback_signature(secret: bytes, *, timestamp: str, body: bytes) -> str:
    digest = hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"v1={digest}"


__all__ = [
    "APP_CALLBACK_EVENTS",
    "callback_body",
    "callback_signature",
    "normalize_app_callback",
    "resolve_callback_secret",
]
