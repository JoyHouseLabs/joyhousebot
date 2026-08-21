"""Async Owner and Installation clients for joyhousebot public API v2."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

import httpx

from joyhousebot_sdk.errors import AuthenticationError, error_from_response
from joyhousebot_sdk.models import Page, Run, RunEvent

T = TypeVar("T")
_RETRYABLE_METHODS = {"GET", "HEAD", "OPTIONS"}


class PublicClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )
        self._access_token: str | None = None
        self._expires_at: datetime | None = None
        self._auth_lock = asyncio.Lock()

    async def __aenter__(self) -> "PublicClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def authenticate(self) -> None:
        raise NotImplementedError

    async def list_entrypoints(self, *, limit: int = 100, cursor: str | None = None) -> Page:
        return Page.parse(await self._json("GET", "/v2/entrypoints", params=_page(limit, cursor)))

    async def get_entrypoint(self, entrypoint_id: str) -> dict[str, Any]:
        return await self._json("GET", f"/v2/entrypoints/{entrypoint_id}")

    async def resolve_entrypoint(self, key: str, *, app_id: str | None = None) -> dict[str, Any]:
        """Resolve a source-stable EntryPoint key to its opaque installation ID."""
        matches = [
            item
            for item in (await self.list_entrypoints()).items
            if item.get("key") == key and (app_id is None or item.get("app_id") == app_id)
        ]
        if len(matches) != 1:
            qualifier = f" in {app_id}" if app_id else ""
            raise ValueError(
                f"expected exactly one installed EntryPoint {key!r}{qualifier}; "
                f"found {len(matches)}"
            )
        return matches[0]

    async def run_entrypoint(
        self,
        key: str,
        input: dict[str, Any],
        *,
        idempotency_key: str,
        app_id: str | None = None,
        session_id: str | None = None,
        client_context: dict[str, Any] | None = None,
    ) -> "RunHandle":
        entrypoint = await self.resolve_entrypoint(key, app_id=app_id)
        return await self.run(
            str(entrypoint["id"]),
            input,
            idempotency_key=idempotency_key,
            session_id=session_id,
            client_context=client_context,
        )

    async def run(
        self,
        entrypoint_id: str,
        input: dict[str, Any],
        *,
        idempotency_key: str,
        session_id: str | None = None,
        client_context: dict[str, Any] | None = None,
    ) -> "RunHandle":
        body: dict[str, Any] = {
            "input": input,
            "idempotency_key": idempotency_key,
            "client_context": dict(client_context or {}),
        }
        if session_id:
            body["session_id"] = session_id
        value = await self._json(
            "POST",
            f"/v2/entrypoints/{entrypoint_id}/runs",
            headers={"Idempotency-Key": idempotency_key},
            json=body,
        )
        return RunHandle(self, Run.parse(value))

    def handle(self, run_id: str) -> "RunHandle":
        return RunHandle(self, Run(run_id, "queued", {}, None, {"id": run_id}))

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._request(method, path, **kwargs)
        if response.status_code == 204:
            return None
        return response.json()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        await self._ensure_token()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self._access_token}"
        attempts = (
            3 if method.upper() in _RETRYABLE_METHODS or headers.get("Idempotency-Key") else 1
        )
        for attempt in range(attempts):
            try:
                response = await self._http.request(method, path, headers=headers, **kwargs)
            except httpx.TransportError:
                if attempt + 1 >= attempts:
                    raise
                await asyncio.sleep(0.1 * (2**attempt))
                continue
            if response.status_code == 401 and attempt == 0:
                self._access_token = None
                await self._ensure_token()
                headers["Authorization"] = f"Bearer {self._access_token}"
                continue
            if response.status_code >= 400:
                try:
                    value = response.json()
                except ValueError:
                    value = None
                error = error_from_response(response.status_code, value)
                if error.retryable and attempt + 1 < attempts:
                    await asyncio.sleep(0.1 * (2**attempt))
                    continue
                raise error
            return response
        raise AssertionError("unreachable")

    async def _ensure_token(self) -> None:
        if not self._token_is_fresh():
            async with self._auth_lock:
                if not self._token_is_fresh():
                    await self.authenticate()

    def _token_is_fresh(self) -> bool:
        now = datetime.now(timezone.utc)
        return bool(
            self._access_token
            and self._expires_at
            and (self._expires_at - now).total_seconds() >= 30
        )

    def _set_token(self, value: dict[str, Any]) -> None:
        self._access_token = str(value["access_token"])
        self._expires_at = datetime.fromisoformat(str(value["expires_at"]).replace("Z", "+00:00"))


class AppClient(PublicClient):
    def __init__(
        self,
        base_url: str,
        *,
        client_id: str,
        client_secret: str,
        installation_id: str,
        scopes: tuple[str, ...] = ("apps.read", "apps.launch", "runs.read", "runs.write"),
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, **kwargs)
        self.client_id = client_id
        self._client_secret = client_secret
        self.installation_id = installation_id
        self.scopes = scopes

    @classmethod
    def from_env(cls, **kwargs: Any) -> "AppClient":
        return cls(
            _required_env("JOYHOUSEBOT_URL"),
            client_id=_required_env("JOYHOUSEBOT_CLIENT_ID"),
            client_secret=_required_env("JOYHOUSEBOT_CLIENT_SECRET"),
            installation_id=_required_env("JOYHOUSEBOT_INSTALLATION_ID"),
            **kwargs,
        )

    async def authenticate(self) -> None:
        response = await self._http.post(
            "/v2/app-auth/token",
            json={
                "client_id": self.client_id,
                "client_secret": self._client_secret,
                "installation_id": self.installation_id,
                "scopes": list(self.scopes),
                "ttl_seconds": 900,
            },
        )
        if response.status_code >= 400:
            raise AuthenticationError.from_response(response.status_code, response.json())
        self._set_token(response.json())


class OwnerClient(PublicClient):
    def __init__(
        self,
        base_url: str,
        *,
        client_id: str,
        subject_token: str | Callable[[], str | Awaitable[str]],
        scopes: tuple[str, ...] = (
            "apps.read",
            "apps.install",
            "apps.launch",
            "runs.read",
            "runs.write",
        ),
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, **kwargs)
        self.client_id = client_id
        self._subject_token = subject_token
        self.scopes = scopes
        self._refresh_token: str | None = None

    @classmethod
    def from_env(cls, **kwargs: Any) -> "OwnerClient":
        return cls(
            _required_env("JOYHOUSEBOT_URL"),
            client_id=_required_env("JOYHOUSEBOT_OWNER_CLIENT_ID"),
            subject_token=_required_env("JOYHOUSEBOT_OWNER_SUBJECT_TOKEN"),
            **kwargs,
        )

    async def authenticate(self) -> None:
        used_refresh = bool(self._refresh_token)
        if used_refresh:
            response = await self._http.post(
                "/v2/owner-auth/refresh",
                json={"client_id": self.client_id, "refresh_token": self._refresh_token},
            )
        else:
            token = self._subject_token() if callable(self._subject_token) else self._subject_token
            if isinstance(token, Awaitable):
                token = await token
            response = await self._http.post(
                "/v2/owner-auth/token",
                json={
                    "client_id": self.client_id,
                    "subject_token": token,
                    "scopes": list(self.scopes),
                },
            )
        if response.status_code >= 400 and used_refresh:
            self._refresh_token = None
            token = self._subject_token() if callable(self._subject_token) else self._subject_token
            if isinstance(token, Awaitable):
                token = await token
            response = await self._http.post(
                "/v2/owner-auth/token",
                json={
                    "client_id": self.client_id,
                    "subject_token": token,
                    "scopes": list(self.scopes),
                },
            )
        if response.status_code >= 400:
            raise AuthenticationError.from_response(response.status_code, response.json())
        value = response.json()
        self._refresh_token = str(value["refresh_token"])
        self._set_token(value)

    async def revoke(self) -> None:
        await self._json("POST", "/v2/owner-auth/revoke")
        self._access_token = None
        self._refresh_token = None

    async def list_apps(self) -> Page:
        return Page.parse(await self._json("GET", "/v2/apps"))

    async def ensure_app(
        self,
        app_id: str,
        version: str,
        *,
        configuration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        installed = [
            item
            for item in (await self.list_apps()).items
            if item.get("app_id") == app_id
            and item.get("version") == version
            and item.get("status") == "active"
        ]
        if installed:
            return installed[0]
        return await self._json(
            "POST",
            f"/v2/apps/{app_id}/install",
            json={"version": version, "configuration": dict(configuration or {})},
        )


class RunHandle:
    def __init__(self, client: PublicClient, run: Run) -> None:
        self.client = client
        self.current = run

    @property
    def id(self) -> str:
        return self.current.id

    async def get(self) -> Run:
        self.current = Run.parse(await self.client._json("GET", f"/v2/runs/{self.id}"))
        return self.current

    async def wait(self, *, timeout_seconds: float = 300, poll_seconds: float = 1) -> Run:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            run = await self.get()
            if run.terminal:
                return run
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"Run did not reach a terminal state: {self.id}")
            await asyncio.sleep(max(0.05, poll_seconds))

    async def cancel(self) -> Run:
        self.current = Run.parse(await self.client._json("POST", f"/v2/runs/{self.id}/cancel"))
        return self.current

    async def artifacts(self, *, limit: int = 100, cursor: str | None = None) -> Page:
        return Page.parse(
            await self.client._json(
                "GET", f"/v2/runs/{self.id}/artifacts", params=_page(limit, cursor)
            )
        )

    async def approvals(self, *, limit: int = 100, cursor: str | None = None) -> Page:
        return Page.parse(
            await self.client._json(
                "GET", f"/v2/runs/{self.id}/approvals", params=_page(limit, cursor)
            )
        )

    async def operations(self) -> Page:
        return Page.parse(
            await self.client._json("GET", f"/v2/runs/{self.id}/operations")
        )

    async def decide(
        self, approval_id: str, decision: str, *, note: str | None = None
    ) -> dict[str, Any]:
        return await self.client._json(
            "POST",
            f"/v2/approvals/{approval_id}/decisions",
            json={"decision": decision, "note": note},
        )

    async def inputs(self, *, limit: int = 100, cursor: str | None = None) -> Page:
        return Page.parse(
            await self.client._json(
                "GET", f"/v2/runs/{self.id}/inputs", params=_page(limit, cursor)
            )
        )

    async def answer(self, input_request_id: str, answers: dict[str, Any]) -> dict[str, Any]:
        return await self.client._json(
            "POST",
            f"/v2/runs/{self.id}/inputs",
            json={"input_request_id": input_request_id, "answers": answers},
        )

    async def events(self, *, after_sequence: int = 0) -> AsyncIterator[RunEvent]:
        headers = {"Last-Event-ID": str(after_sequence)} if after_sequence else {}
        await self.client._ensure_token()
        headers["Authorization"] = f"Bearer {self.client._access_token}"
        async with self.client._http.stream(
            "GET", f"/v2/runs/{self.id}/events", headers=headers
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                try:
                    value = json.loads(body)
                except ValueError:
                    value = None
                raise error_from_response(response.status_code, value)
            data: list[str] = []
            async for line in response.aiter_lines():
                if not line:
                    if data:
                        yield RunEvent.parse(json.loads("\n".join(data)))
                        data.clear()
                    continue
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())


def _required_env(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _page(limit: int, cursor: str | None) -> dict[str, Any]:
    return {"limit": limit, **({"cursor": cursor} if cursor else {})}


__all__ = ["AppClient", "OwnerClient", "PublicClient", "RunHandle"]
