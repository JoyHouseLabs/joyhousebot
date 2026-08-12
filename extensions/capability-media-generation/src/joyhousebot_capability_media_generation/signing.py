"""Volcengine OpenAPI HMAC-SHA256 request signing."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hmac(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def canonical_query(params: dict[str, Any]) -> str:
    pairs = sorted((str(key), str(value)) for key, value in params.items())
    return "&".join(
        f"{quote(key, safe='-_.~')}={quote(value, safe='-_.~')}" for key, value in pairs
    )


def sign_openapi_request(
    *,
    method: str,
    url: str,
    params: dict[str, Any],
    body: bytes,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    idempotency_key: str | None = None,
    session_token: str | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Build the signed headers documented by Volcengine OpenAPI."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    request_date = current.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = current.strftime("%Y%m%d")
    parsed = urlsplit(url)
    host = parsed.netloc
    payload_hash = _sha256(body)
    headers = {
        "content-type": "application/json",
        "host": host,
        "x-content-sha256": payload_hash,
        "x-date": request_date,
    }
    if idempotency_key:
        headers["x-idempotency-key"] = idempotency_key
    if session_token:
        headers["x-security-token"] = session_token
    signed_header_names = ";".join(sorted(headers))
    canonical_headers = "".join(
        f"{name}:{' '.join(headers[name].strip().split())}\n"
        for name in sorted(headers)
    )
    canonical_request = "\n".join(
        (
            method.upper(),
            parsed.path or "/",
            canonical_query(params),
            canonical_headers,
            signed_header_names,
            payload_hash,
        )
    )
    scope = f"{date_stamp}/{region}/{service}/request"
    string_to_sign = "\n".join(
        (
            "HMAC-SHA256",
            request_date,
            scope,
            _sha256(canonical_request.encode("utf-8")),
        )
    )
    signing_key = _hmac(
        _hmac(_hmac(_hmac(secret_key.encode("utf-8"), date_stamp), region), service),
        "request",
    )
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    headers["authorization"] = (
        f"HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_header_names}, Signature={signature}"
    )
    return {name.title(): value for name, value in headers.items()}


__all__ = ["canonical_query", "sign_openapi_request"]
