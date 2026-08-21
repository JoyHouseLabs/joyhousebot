"""Short-lived Owner assertion creation for trusted Product backends."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt


@dataclass(frozen=True, slots=True)
class OwnerAssertionSigner:
    issuer: str
    private_key_pem: str
    algorithm: str = "EdDSA"
    audience: str = "joyhousebot-owner"
    lifetime_seconds: int = 120

    def __post_init__(self) -> None:
        if not self.issuer.strip() or not self.private_key_pem.strip():
            raise ValueError("Owner assertion issuer and private key are required")
        if self.algorithm not in {"EdDSA", "RS256"}:
            raise ValueError("Owner assertion algorithm must be EdDSA or RS256")
        if not 30 <= self.lifetime_seconds <= 300:
            raise ValueError("Owner assertion lifetime must be between 30 and 300 seconds")

    def sign(self, user_id: str) -> str:
        subject = user_id.strip()
        if not subject:
            raise ValueError("Owner assertion user_id is required")
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {
                "iss": self.issuer,
                "sub": subject,
                "aud": self.audience,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(seconds=self.lifetime_seconds)).timestamp()),
                "jti": f"owner_{uuid4().hex}",
            },
            self.private_key_pem,
            algorithm=self.algorithm,
        )


__all__ = ["OwnerAssertionSigner"]
