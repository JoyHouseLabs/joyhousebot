"""Policy and assertion verification for first-party Owner delegation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

import jwt

OWNER_TOKEN_AUDIENCE = "joyhousebot-owner"
DELEGATABLE_OWNER_SCOPES = frozenset(
    {"apps.read", "apps.install", "apps.launch", "runs.read", "runs.write"}
)
OWNER_ASSERTION_ALGORITHMS = frozenset({"EdDSA", "RS256"})


def normalize_owner_scopes(values: Iterable[str]) -> tuple[str, ...]:
    scopes = tuple(sorted({str(item).strip().lower() for item in values if str(item).strip()}))
    if not scopes:
        raise ValueError("at least one Owner delegation scope is required")
    unsupported = sorted(set(scopes) - DELEGATABLE_OWNER_SCOPES)
    if unsupported:
        raise ValueError(f"Owner delegation contains unsupported scopes: {unsupported}")
    return scopes


def verify_owner_assertion(
    assertion: str,
    *,
    public_key_pem: str,
    algorithm: str,
    issuer: str,
) -> dict[str, Any]:
    if algorithm not in OWNER_ASSERTION_ALGORITHMS:
        raise ValueError("Owner assertion algorithm is not supported")
    try:
        claims = jwt.decode(
            assertion,
            public_key_pem,
            algorithms=[algorithm],
            audience=OWNER_TOKEN_AUDIENCE,
            issuer=issuer,
            options={"require": ["iss", "sub", "aud", "iat", "exp", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise ValueError("Owner assertion is invalid") from exc
    now = datetime.now(timezone.utc).timestamp()
    issued_at = float(claims["iat"])
    expires_at = float(claims["exp"])
    if issued_at > now + 30 or issued_at < now - 300 or expires_at > issued_at + 300:
        raise ValueError("Owner assertion lifetime is invalid")
    subject = str(claims["sub"]).strip()
    jti = str(claims["jti"]).strip()
    if not subject or len(subject) > 128 or not jti or len(jti) > 256:
        raise ValueError("Owner assertion subject or jti is invalid")
    return {"user_id": subject, "jti": jti, "expires_at": expires_at}


__all__ = [
    "DELEGATABLE_OWNER_SCOPES",
    "OWNER_ASSERTION_ALGORITHMS",
    "OWNER_TOKEN_AUDIENCE",
    "normalize_owner_scopes",
    "verify_owner_assertion",
]
