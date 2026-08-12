"""Async client for the public App delegation and Entry Point data plane."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

_TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


class AppRuntimeClient:
    def __init__(
        self,
        base_url: str,
        *,
        client_id: str,
        client_secret: str,
        grant_id: str,
        scopes: tuple[str, ...] = ("apps.read", "apps.launch", "runs.read"),
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.client_id = client_id
        self._client_secret = client_secret
        self.grant_id = grant_id
        self.scopes = tuple(scopes)
        self._access_token: str | None = None
        self._expires_at: datetime | None = None
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def __aenter__(self) -> "AppRuntimeClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def exchange(self, *, ttl_seconds: int = 900) -> dict[str, Any]:
        response = await self._client.post(
            "/v1/app-auth/token",
            json={
                "client_id": self.client_id,
                "client_secret": self._client_secret,
                "grant_id": self.grant_id,
                "scopes": list(self.scopes),
                "ttl_seconds": ttl_seconds,
            },
        )
        response.raise_for_status()
        value = dict(response.json())
        self._access_token = str(value["access_token"])
        self._expires_at = datetime.fromisoformat(
            str(value["expires_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        return value

    async def list_apps(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/v1/apps")
        return [dict(item) for item in response.json()["items"]]

    async def launch(
        self,
        installation_id: str,
        content: str,
        *,
        idempotency_key: str,
        entrypoint_id: str | None = None,
        session_id: str | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise ValueError("App launch requires a stable idempotency_key")
        response = await self._request(
            "POST",
            f"/v1/apps/{installation_id}/runs",
            headers={"Idempotency-Key": idempotency_key},
            json={
                "entrypoint_id": entrypoint_id,
                "session_id": session_id,
                "input": {"content": content},
                "inputs": dict(inputs or {}),
            },
        )
        return dict(response.json())

    async def get_run(self, run_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/v1/runs/{run_id}")
        return dict(response.json())

    async def wait_run(
        self,
        run_id: str,
        *,
        timeout_seconds: float = 300.0,
        poll_seconds: float = 1.0,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            run = await self.get_run(run_id)
            if str(run.get("status")) in _TERMINAL:
                return run
            if loop.time() >= deadline:
                raise TimeoutError(f"Run did not reach a terminal state: {run_id}")
            await asyncio.sleep(max(0.05, poll_seconds))

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        await self._ensure_token()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self._access_token}"
        response = await self._client.request(method, path, headers=headers, **kwargs)
        if response.status_code == 401:
            self._access_token = None
            await self._ensure_token()
            headers["Authorization"] = f"Bearer {self._access_token}"
            response = await self._client.request(method, path, headers=headers, **kwargs)
        response.raise_for_status()
        return response

    async def _ensure_token(self) -> None:
        now = datetime.now(timezone.utc)
        if (
            self._access_token is None
            or self._expires_at is None
            or (self._expires_at - now).total_seconds() < 30
        ):
            await self.exchange()


__all__ = ["AppRuntimeClient"]
