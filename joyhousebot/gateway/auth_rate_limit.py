"""In-memory sliding-window rate limiter for gateway auth (OpenClaw-aligned)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


AUTH_RATE_LIMIT_SCOPE_DEFAULT = "default"
AUTH_RATE_LIMIT_SCOPE_SHARED_SECRET = "shared-secret"
AUTH_RATE_LIMIT_SCOPE_DEVICE_TOKEN = "device-token"


@dataclass
class RateLimitEntry:
    attempts: list[float] = field(default_factory=list)
    locked_until: float | None = None


@dataclass
class RateLimitCheckResult:
    allowed: bool
    remaining: int
    retry_after_ms: int


def _is_loopback(ip: str | None) -> bool:
    if not ip or not ip.strip():
        return False
    ip = ip.strip()
    return ip in ("127.0.0.1", "::1", "localhost")


class AuthRateLimiter:
    """Sliding-window rate limiter for auth attempts per (scope, ip)."""

    def __init__(
        self,
        *,
        max_attempts: int = 10,
        window_ms: int = 60_000,
        lockout_ms: int = 300_000,
        exempt_loopback: bool = True,
    ):
        self._max_attempts = max_attempts
        self._window_ms = window_ms
        self._lockout_ms = lockout_ms
        self._exempt_loopback = exempt_loopback
        self._entries: dict[str, RateLimitEntry] = {}

    def _key(self, ip: str | None, scope: str) -> str:
        ip = (ip or "").strip() or "unknown"
        scope = (scope or AUTH_RATE_LIMIT_SCOPE_DEFAULT).strip() or AUTH_RATE_LIMIT_SCOPE_DEFAULT
        return f"{scope}:{ip}"

    def _slide(self, entry: RateLimitEntry, now: float) -> None:
        cutoff = now - (self._window_ms / 1000.0)
        entry.attempts = [t for t in entry.attempts if t > cutoff]

    def check(self, ip: str | None, scope: str = AUTH_RATE_LIMIT_SCOPE_DEFAULT) -> RateLimitCheckResult:
        if self._exempt_loopback and _is_loopback(ip):
            return RateLimitCheckResult(allowed=True, remaining=self._max_attempts, retry_after_ms=0)
        key = self._key(ip, scope)
        entry = self._entries.get(key)
        if not entry:
            return RateLimitCheckResult(allowed=True, remaining=self._max_attempts, retry_after_ms=0)
        now = time.time()
        if entry.locked_until and now < entry.locked_until:
            return RateLimitCheckResult(
                allowed=False,
                remaining=0,
                retry_after_ms=int((entry.locked_until - now) * 1000),
            )
        if entry.locked_until and now >= entry.locked_until:
            entry.locked_until = None
            entry.attempts = []
        self._slide(entry, now)
        remaining = max(0, self._max_attempts - len(entry.attempts))
        return RateLimitCheckResult(allowed=remaining > 0, remaining=remaining, retry_after_ms=0)

    def record_failure(self, ip: str | None, scope: str = AUTH_RATE_LIMIT_SCOPE_DEFAULT) -> None:
        if self._exempt_loopback and _is_loopback(ip):
            return
        key = self._key(ip, scope)
        entry = self._entries.setdefault(key, RateLimitEntry())
        now = time.time()
        if entry.locked_until and now < entry.locked_until:
            return
        self._slide(entry, now)
        entry.attempts.append(now)
        if len(entry.attempts) >= self._max_attempts:
            entry.locked_until = now + (self._lockout_ms / 1000.0)

    def reset(self, ip: str | None, scope: str = AUTH_RATE_LIMIT_SCOPE_DEFAULT) -> None:
        key = self._key(ip, scope)
        self._entries.pop(key, None)

    def size(self) -> int:
        return len(self._entries)
