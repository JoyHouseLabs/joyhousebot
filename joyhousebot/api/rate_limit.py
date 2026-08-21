"""Database-backed API admission limits shared by every gateway replica."""

from __future__ import annotations

import asyncio
import hashlib
import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from joyhousebot.api.public_v2_errors import public_error_response

_DEFAULT_RATE_PER_MINUTE = 120
_EXEMPT_PATHS = {"/healthz", "/readyz"}


def _rate_limit_per_minute() -> int:
    raw = os.getenv("JOYHOUSEBOT_API_RATE_PER_MINUTE", "")
    try:
        value = int(raw) if raw else _DEFAULT_RATE_PER_MINUTE
    except ValueError:
        value = _DEFAULT_RATE_PER_MINUTE
    return max(1, value)


def _error_response(
    status: int, code: str, message: str, *, public_v2: bool = False
) -> JSONResponse:
    if public_v2:
        return public_error_response(status, code, message)
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, per_minute: int | None = None) -> None:
        super().__init__(app)
        self.per_minute = per_minute or _rate_limit_per_minute()

    async def _allowed(self, store, key: str, *, increment: bool) -> bool:
        return await asyncio.to_thread(
            store.check_api_rate_limit,
            key,
            limit=self.per_minute,
            window_seconds=60,
            increment=increment,
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        container = getattr(request.app.state, "container", None)
        public_v2 = request.url.path == "/v2" or request.url.path.startswith("/v2/")
        if container is None:
            return _error_response(
                503, "not_ready", "gateway is not ready", public_v2=public_v2
            )

        authorization = request.headers.get("authorization") or ""
        scheme, _, token = authorization.partition(" ")
        token = token.strip() if scheme.lower() == "bearer" else ""
        client_host = request.client.host if request.client else "unknown"
        fail_key = f"authfail:{client_host}"
        key = (
            f"token:{hashlib.sha256(token.encode('utf-8')).hexdigest()[:24]}"
            if token
            else f"ip:{client_host}"
        )
        try:
            primary_allowed, auth_allowed = await asyncio.gather(
                self._allowed(container.store, key, increment=True),
                self._allowed(container.store, fail_key, increment=False),
            )
        except Exception:
            return _error_response(
                503,
                "storage_unavailable",
                "gateway storage unavailable",
                public_v2=public_v2,
            )
        if not primary_allowed or not auth_allowed:
            return _error_response(
                429, "rate_limited", "rate limit exceeded", public_v2=public_v2
            )

        response = await call_next(request)
        if response.status_code in (401, 403):
            try:
                await self._allowed(container.store, fail_key, increment=True)
            except Exception:
                return _error_response(
                    503,
                    "storage_unavailable",
                    "gateway storage unavailable",
                    public_v2=public_v2,
                )
        return response
